#!/usr/bin/env python3
"""c08-atomics: fields accessed atomically, volatile fields, and accessors that hand out a plain scalar
reference to a field (RULEBOOK 0.8 原子操作, §2.7 原子量 / 原子字段的规则, §5.2 #8).

Sites: every call of an ob_atomic.h macro (`ATOMIC_*`, read from the #defines of
src/oblib/lib/atomic/ob_atomic.h), of `inc_update`/`dec_update`, and of the `__atomic_*`/`__sync_*`
builtins with an address operand, in comment/string-stripped first-party sources (ob_atomic.h's own
definitions excluded).  The first argument is the operand.

Operand mapping: casts and parentheses are removed; `&chain` is parsed as base(.m | ->m | [i])*.  The base
is resolved in the enclosing function (astdb definition record: parameters, fields of its class and bases,
static members, locals by regex, `this`), then every `.m`/`->m` step through the field types of the
records found (pointer/array/reference stripped); the last member is the field.  An unresolvable chain
falls back to the field name: one candidate in the database -> mapped (pretag by-name), several ->
`ambiguous atomic operand` row.  A bare pointer operand (`ATOMIC_LOAD(p)`) is the target of an unknown
object (`unresolved atomic operand`), except an array field that decays (`ATOMIC_FAA(arr_ + i, 1)`).
Sites inside a #define body are resolved at every invocation of that macro found in a function body
(macro parameters replaced by the invocation's arguments).

Rows (symbol `Class::field_` for fields, `Class::name`/`name` for static members/globals,
`<function>#<operand>` for ambiguous/unresolved operands):
  atomic-accessed field | atomic-accessed static member | atomic-accessed global variable
  volatile field                 a volatile-qualified data member never accessed atomically
  scalar reference accessor      a method returning a non-const `bool &`/integer reference to a field
                                 (symbol = the field; the accessor is in the pretag), e.g. has_set_stop()
  ambiguous atomic operand | unresolved atomic operand
Pretag: `macros=<LOAD:3,STORE:1,…>` (ATOMIC_ prefix dropped), `sites=<n>`, `type=<canonical type>`,
`pointer-field`, `volatile`, `plain-reads=<n>`, `plain-writes=<n>`, `addr-taken=<n>`, `ctor-dtor-uses=<n>`
(regex counts over the methods of the owner class and its derived classes, atomic calls blanked),
`publish-pattern` (a pointer stored/CAS'ed right after the stored object was allocated or written
through), `bcas-lazy-init` (CAS of a pointer from NULL/nullptr/0), `bcas-flag` (CAS of a scalar from
false/0), `by-name`, `in-macro=<M>`, `unit=<disposition>`.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
CAT = 'c08-atomics'
ATOMIC_H = 'src/oblib/lib/atomic/ob_atomic.h'
BUILTIN_NO_OPERAND = {'__sync_synchronize', '__atomic_thread_fence', '__atomic_signal_fence',
                      '__atomic_always_lock_free', '__atomic_is_lock_free'}
STORE_LIKE = {'STORE', 'STORE_REL', 'STORE_RLX', 'STORE64', 'SET', 'SETx', 'TAS', 'TASx', '__atomic_store_n',
              '__atomic_exchange_n', '__atomic_store', '__atomic_exchange'}
CAS_LIKE = {'BCAS', 'BCASx', 'BCAS_AR', 'VCAS', 'VCASx', 'VCAS_AR', 'CAS', 'CMP_AND_EXCHANGE',
            '__sync_bool_compare_and_swap', '__sync_val_compare_and_swap', '__atomic_compare_exchange_n',
            '__atomic_compare_exchange'}


def atomic_names():
    names = set()
    for name, defs in S.macro_index().items():
        if name.startswith('ATOMIC_') and any(md.file == ATOMIC_H for md in defs):
            names.add(name)
    return names


def split_args(text, open_idx):
    """Arguments of the call whose '(' is at open_idx, and the index of its ')'."""
    depth = 0
    args, cur = [], []
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch in '([{':
            depth += 1
            if depth > 1:
                cur.append(ch)
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                args.append(''.join(cur).strip())
                return args, i
            cur.append(ch)
        elif ch == ',' and depth == 1:
            args.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    return None, None


RX_CAST_C = re.compile(r'^\(\s*(?:const\s+|volatile\s+|struct\s+|unsigned\s+|signed\s+)*[A-Za-z_][\w:<>,\s]*?[\s*&]*\*\s*\)\s*')
RX_CAST_CXX = re.compile(r'^(?:reinterpret_cast|static_cast|const_cast)\s*<[^()]*>\s*\(')


def strip_operand(e):
    e = ' '.join(e.split())
    for _ in range(10):
        before = e
        while e.startswith('(') and e.endswith(')') and _balanced(e[1:-1]):
            e = e[1:-1].strip()
        m = RX_CAST_C.match(e)
        if m and m.end() < len(e):
            e = e[m.end():].strip()
        m = RX_CAST_CXX.match(e)
        if m and e.endswith(')') and _balanced(e[m.end():-1]):
            e = e[m.end():-1].strip()
        if e == before:
            break
    return e


def _balanced(s):
    d = 0
    for ch in s:
        if ch in '([{':
            d += 1
        elif ch in ')]}':
            d -= 1
            if d < 0:
                return False
    return d == 0


RX_CHAIN_HEAD = re.compile(r'^(?:\(\s*\*\s*([A-Za-z_]\w*)\s*\)|(this)|([A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*))')


def parse_chain(e):
    """(base, steps) of an access chain base(.m|->m|[i])*; steps = [('.',m)|('->',m)|('[',expr)]; None if not."""
    m = RX_CHAIN_HEAD.match(e)
    if not m:
        return None
    if m.group(1):
        base, steps = m.group(1), [('deref', None)]
    else:
        base, steps = (m.group(2) or re.sub(r'\s+', '', m.group(3))), []
    i = m.end()
    n = len(e)
    while i < n:
        while i < n and e[i] == ' ':
            i += 1
        if i >= n:
            break
        if e.startswith('->', i):
            mm = re.match(r'->\s*([A-Za-z_]\w*)', e[i:])
            if not mm:
                return None
            steps.append(('->', mm.group(1)))
            i += mm.end()
        elif e[i] == '.':
            mm = re.match(r'\.\s*([A-Za-z_]\w*)', e[i:])
            if not mm:
                return None
            steps.append(('.', mm.group(1)))
            i += mm.end()
        elif e[i] == '[':
            d, j = 0, i
            while j < n:
                if e[j] == '[':
                    d += 1
                elif e[j] == ']':
                    d -= 1
                    if d == 0:
                        break
                j += 1
            if j >= n:
                return None
            steps.append(('[', e[i + 1:j]))
            i = j + 1
        else:
            return None
    return base, steps


class Resolver(object):
    def __init__(self, db, env):
        self.db, self.env = db, env
        self.fields_by_name = collections.defaultdict(list)
        for f in db.by_kind('field'):
            if not f.get('generated'):
                self.fields_by_name[f['name']].append(f)
        self.vars_by_name = collections.defaultdict(list)
        for v in db.by_kind('var'):
            if not v.get('generated') and v.get('is_definition', 1) is not None:
                self.vars_by_name[v['name']].append(v)
        self.static_members = collections.defaultdict(dict)     # record usr -> name -> var record
        for v in db.by_kind('var'):
            if v.get('is_member') and v.get('parent_usr') and not v.get('generated'):
                self.static_members[v['parent_usr']].setdefault(v['name'], v)

    def _static_member(self, rec_usr, name):
        for u in [rec_usr] + self.db.ancestors(rec_usr):
            v = self.static_members.get(u, {}).get(name)
            if v is not None:
                return v
        return None

    def _element_type(self, t):
        t = S.L._clean(t)
        m = re.match(r'^(.*?)\s*\[[^\]]*\]$', t)
        if m:
            return m.group(1)
        if re.search(r'\*\s*$', t):
            return re.sub(r'\*\s*$', '', t).strip()
        return None

    def resolve(self, defrec, body, e):
        """('field', rec) | ('static', var) | ('global', var) | ('ambiguous', [recs]) | ('unresolved', reason)."""
        e = strip_operand(e)
        addr = e.startswith('&')
        if addr:
            e = strip_operand(e[1:])
        else:
            mm = re.match(r'^(.*?)\s*\+\s*[^+]+$', e)
            if mm and not mm.group(1).endswith(')'):
                e2 = mm.group(1).strip()
            else:
                e2 = e
            ch = parse_chain(e2)
            if ch is None:
                return ('unresolved', 'pointer-expression')
            r = self._walk(defrec, body, ch)
            if r and r[0] == 'field' and re.search(r'\]$', S.L._clean(r[1].get('canonical_type') or '')):
                return r                      # array field decays to a pointer to its first element
            if r and r[0] == 'field':
                return ('unresolved', 'pointer-field:%s::%s' % (S.short_qname(r[1].get('parent_qname', '?')), r[1]['name']))
            return ('unresolved', 'pointer-operand')
        ch = parse_chain(e)
        if ch is None:
            mm = re.search(r'(?:\.|->)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', e)
            if not mm:
                return ('unresolved', 'complex-lvalue')
            steps, name, base = [('.', mm.group(1))], mm.group(1), None
        else:
            r = self._walk(defrec, body, ch)
            if r is not None:
                return r
            base, steps = ch
            last = [s[1] for s in steps if s[0] in ('.', '->')]
            name = last[-1] if last else (base if not steps else None)
        if name is None:
            return ('unresolved', 'no-member')
        cands = self.fields_by_name.get(name, [])
        if len(cands) == 1:
            return ('field', cands[0], 'by-name')
        if len(cands) > 1:
            return ('ambiguous', cands)
        vs = [v for v in self.vars_by_name.get(name, []) if not v.get('is_member')]
        if not steps and len({v['usr'] for v in vs}) == 1:
            return ('global', vs[0])
        return ('unresolved', 'local-or-param' if not steps else 'unknown-member')

    def _walk(self, defrec, body, chain):
        base, steps = chain
        env = self.env
        cu = env.class_of(defrec) if defrec else None
        cur_type, cur_rec = None, None
        if base == 'this':
            if cu is None:
                return None
            cur_rec = cu
        elif '::' in base:
            # Class::static_member or ns::global
            parts = base.split('::')
            nm = parts[-1]
            q = '::'.join(parts[:-1])
            ru = env.record_of_type(q)
            if ru is not None:
                v = self._static_member(ru, nm)
                if v is not None and not steps:
                    return ('static', v)
                if v is not None:
                    cur_type = v.get('canonical_type') or v.get('type')
                else:
                    return None
            else:
                vs = [v for v in self.vars_by_name.get(nm, []) if v['qname'].endswith(base) and not v.get('is_member')]
                if len({v['usr'] for v in vs}) == 1:
                    if not steps:
                        return ('global', vs[0])
                    cur_type = vs[0].get('canonical_type') or vs[0].get('type')
                else:
                    return None
        else:
            if cu is not None and steps and all(op == '[' for op, _a in steps):
                f = env.fields(cu).get(base)
                if f is not None:
                    return ('field', f)       # an element of an array (or container) field
            if not steps:
                if cu is not None:
                    f = env.fields(cu).get(base)
                    if f is not None:
                        return ('field', f)
                    v = self._static_member(cu, base)
                    if v is not None:
                        return ('static', v)
                t, origin = env.var_type(defrec, body, base) if defrec else (None, None)
                if t is not None:
                    return ('unresolved', 'local' if origin == 'local' else 'param')
                vs = [v for v in self.vars_by_name.get(base, []) if not v.get('is_member')]
                if len({v['usr'] for v in vs}) == 1:
                    return ('global', vs[0])
                return None
            t, origin = env.var_type(defrec, body, base) if defrec else (None, None)
            if t is None and cu is not None:
                v = self._static_member(cu, base)
                if v is not None:
                    t = v.get('canonical_type') or v.get('type')
            if t is None:
                vs = [v for v in self.vars_by_name.get(base, []) if not v.get('is_member')]
                if len({v['usr'] for v in vs}) == 1:
                    t = vs[0].get('canonical_type') or vs[0].get('type')
            if t is None:
                # an object-like macro naming a singleton, e.g. GCTX -> ObGlobalContext::get_instance()
                for md in S.macro_index().get(base, []):
                    if md.params is None:
                        mm = re.search(r'([A-Za-z_][\w:]*)\s*::\s*(?:get_instance|instance)\s*\(\s*\)', md.text[md.body_off:])
                        if mm:
                            ru = env.record_of_type(mm.group(1).lstrip(':'))
                            if ru is not None:
                                cur_rec = ru
                                break
                if cur_rec is None:
                    return None
            else:
                cur_type = t
        last_field = None
        for k, (op, arg) in enumerate(steps):
            if op == 'deref':
                if cur_type is None:
                    return None
                cur_type = self._element_type(cur_type) or cur_type
                continue
            if op == '[':
                if cur_rec is not None and cur_type is None:
                    return None
                if cur_type is None:
                    return None
                et = self._element_type(cur_type)
                if et is None:
                    return None
                cur_type, cur_rec = et, None
                continue
            if cur_rec is None:
                if cur_type is None:
                    return None
                cur_rec = env.record_of_type(re.sub(r'\[[^\]]*\]', '', cur_type))
                if cur_rec is None:
                    return None
            f = env.fields(cur_rec).get(arg)
            if f is None:
                v = self._static_member(cur_rec, arg)
                if v is not None and k == len(steps) - 1:
                    return ('static', v)
                return None
            last_field = f
            if k == len(steps) - 1 or all(op == '[' for op, _a in steps[k + 1:]):
                return ('field', f)
            cur_type = f.get('canonical_type') or f.get('type')
            cur_rec = None
        if last_field is not None:
            return ('field', last_field)
        return None


RX_SITE_TMPL = r'\b(%s)\s*\('


def main():
    db = S.load_db(astdb.FUNC_KINDS + astdb.RECORD_KINDS + ('field', 'var'))
    env = S.TypeEnv(db)
    res = Resolver(db, env)
    names = sorted(atomic_names())
    names_all = names + ['inc_update', 'dec_update']
    rx_site = re.compile(r'\b(%s|__atomic_\w+|__sync_\w+)\s*\(' % '|'.join(re.escape(n) for n in sorted(names_all, key=len, reverse=True)))
    defs = S.definitions_with_bodies(db)
    defs_by_file = collections.defaultdict(list)
    for d, sp in defs:
        defs_by_file[d['file']].append((sp[0], sp[1], d))
    for v in defs_by_file.values():
        v.sort(key=lambda x: (x[0], -x[1]))
    midx = S.macro_index()
    macro_by_line = {}
    for nm, mds in midx.items():
        for md in mds:
            for k in range(md.nlines):
                macro_by_line[(md.file, md.line + k)] = md

    def enclosing_def(path, off):
        best = None
        for o, c, d in defs_by_file.get(path, ()):
            if o > off:
                break
            if o < off < c and (best is None or o >= best[0]):
                best = (o, c, d)
        return best

    sites = []     # (path, line, macro, operand, args, context)
    for p in S.first_party_code():
        if p == ATOMIC_H:
            continue
        text = L.stripped(p)
        if 'ATOMIC_' not in text and '__atomic_' not in text and '__sync_' not in text and '_update' not in text:
            continue
        for m in rx_site.finditer(text):
            nm = m.group(1)
            if nm in BUILTIN_NO_OPERAND:
                continue
            args, close = split_args(text, m.end() - 1)
            if not args or not args[0]:
                continue
            line = L.line_of(p, m.start())
            md = macro_by_line.get((p, line))
            if md is not None and md.name in names_all:
                continue
            sites.append((p, line, nm, args[0], args, m.start(), md))
    # ---- resolve
    agg = collections.OrderedDict()      # key -> info
    amb = collections.OrderedDict()
    unres = collections.OrderedDict()
    macro_invocations = {}

    def invocations(md):
        if md.name in macro_invocations:
            return macro_invocations[md.name]
        out = []
        rx = re.compile(r'\b%s\b\s*(\()?' % re.escape(md.name))
        for path in sorted(defs_by_file):
            text = L.stripped(path)
            if md.name not in text:
                continue
            for mm in rx.finditer(text):
                if md.params is not None and not mm.group(1):
                    continue
                if (path, L.line_of(path, mm.start())) in macro_by_line:
                    continue          # inside another #define
                enc = enclosing_def(path, mm.start())
                args = None
                if md.params is not None:
                    args, _c = split_args(text, mm.end() - 1)
                if enc is None:
                    # an invocation at class scope (a macro that defines members): the class is the context
                    rec = db.enclosing(path, L.line_of(path, mm.start()), astdb.RECORD_KINDS)
                    if rec is None:
                        continue
                    pseudo = {'parent_usr': rec['usr'], 'params': [], 'qname': rec['qname'], 'file': path,
                              'kind': 'method'}
                    out.append((pseudo, mm.start(), mm.start(), args, L.line_of(path, mm.start())))
                    continue
                ob, cb, d = enc
                out.append((d, ob, cb, args, L.line_of(path, mm.start())))
        macro_invocations[md.name] = out
        return out

    def substitute(operand, md, args):
        if not md.params or args is None:
            return operand
        mapping = {}
        for k, prm in enumerate(md.params):
            if prm == '...' or prm.endswith('...'):
                break
            if k < len(args):
                mapping[prm] = args[k]
        if not mapping:
            return operand
        rx = re.compile(r'(?<![\w#])(%s)(?![\w#])' % '|'.join(re.escape(x) for x in sorted(mapping, key=len, reverse=True)))
        return rx.sub(lambda mm: '(%s)' % mapping[mm.group(1)], operand)

    def record_hit(r, site, fn_label, by_macro=None):
        p, line, nm, operand, args, off, md = site
        kind = r[0]
        short = nm[7:] if nm.startswith('ATOMIC_') else nm
        if kind in ('field', 'static', 'global'):
            rec = r[1]
            key = rec['usr']
            info = agg.setdefault(key, {'rec': rec, 'kind': kind, 'macros': collections.Counter(), 'sites': set(),
                                        'by_name': False, 'publish': False, 'lazy': False, 'flag': False,
                                        'in_macro': set()})
            info['macros'][short] += 1
            info['sites'].add((p, line))
            if len(r) > 2 and r[2] == 'by-name':
                info['by_name'] = True
            if by_macro:
                info['in_macro'].add(by_macro)
            return key
        label = '%s#%s' % (fn_label, ' '.join(operand.split()))
        if kind == 'ambiguous':
            ent = amb.setdefault((p, label), {'line': line, 'macros': collections.Counter(),
                                              'cands': sorted({S.short_qname(c['parent_qname']) + '::' + c['name'] for c in r[1]})})
            ent['macros'][short] += 1
        else:
            ent = unres.setdefault((p, label), {'line': line, 'macros': collections.Counter(), 'reason': r[1]})
            ent['macros'][short] += 1
        return None

    publish_checks = []
    tus0 = set(S.tus0_files())
    regex_fields = collections.OrderedDict()

    def decl_line(p, name):
        m = re.search(r'(?m)^[ \t]*(?:[\w:<>,*&]+[ \t]+)+[*&]*%s\s*(?:\[[^\]]*\]\s*)?(?:=[^;]*|\{[^;]*\})?;' % re.escape(name),
                      L.stripped(p))
        return L.line_of(p, m.start()) if m else None

    for site in sites:
        p, line, nm, operand, args, off, md = site
        if md is not None:
            invs = invocations(md)
            hit = False
            for d, ob, cb, iargs, iline in invs:
                body = L.stripped(d['file'])[ob:cb + 1]
                op2 = substitute(operand, md, iargs)
                r = res.resolve(d, body, op2)
                if r[0] in ('field', 'static', 'global'):
                    record_hit(r, site, 'macro:' + md.name, by_macro=md.name)
                    hit = True
            if not hit:
                record_hit(('unresolved', 'macro-body(%d invocations)' % len(invs)), site, 'macro:' + md.name)
            continue
        enc = enclosing_def(p, off)
        if enc is None:
            fn = L.site_owner(p, off)
            if p in tus0:
                # file no TU parses: a plain member operand belongs to the enclosing class of the scope parser
                op = strip_operand(operand)
                mm = re.match(r'^&\s*\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\)?(?:\s*\[[^\]]*\])*$', op)
                cls = fn.rsplit('::', 1)[0] if fn and '::' in fn and not fn.startswith('macro:') else None
                if mm and cls:
                    k = (p, '%s::%s' % (cls, mm.group(1)))
                    ent = regex_fields.setdefault(k, {'line': decl_line(p, mm.group(1)) or line,
                                                      'macros': collections.Counter(), 'sites': set()})
                    ent['macros'][nm[7:] if nm.startswith('ATOMIC_') else nm] += 1
                    ent['sites'].add((p, line))
                    continue
            r = res.resolve(None, '', operand)
            record_hit(r, site, fn)
            continue
        ob, cb, d = enc
        body = L.stripped(p)[ob:cb + 1]
        r = res.resolve(d, body, operand)
        key = record_hit(r, site, S.short_qname(d['qname']))
        if key is not None:
            publish_checks.append((key, nm, args, body, off - ob))
    # ---- publish / lazy-init / flag patterns
    for key, nm, args, body, rel in publish_checks:
        info = agg[key]
        t = S.L._clean(info['rec'].get('canonical_type') or info['rec'].get('type') or '')
        is_ptr = t.endswith('*')
        short = nm[7:] if nm.startswith('ATOMIC_') else nm
        if short in CAS_LIKE or nm in CAS_LIKE:
            cmpv = strip_operand(args[1]) if len(args) > 1 else ''
            if is_ptr and cmpv in ('NULL', 'nullptr', '0', 'nullptr_t()'):
                info['lazy'] = True
            elif not is_ptr and cmpv in ('false', '0'):
                info['flag'] = True
        if is_ptr and (short in STORE_LIKE or nm in STORE_LIKE or short in CAS_LIKE or nm in CAS_LIKE):
            val = args[2] if (short in CAS_LIKE or nm in CAS_LIKE) and len(args) > 2 else (args[1] if len(args) > 1 else '')
            val = strip_operand(val)
            if re.match(r'^[A-Za-z_]\w*$', val) and val not in ('NULL', 'nullptr'):
                before = body[:rel]
                if re.search(r'\b%s\s*=\s*(?:new\b|OB_NEW\w*|op_alloc\w*|op_reclaim_alloc|[\w.>-]*alloc\w*\s*\(|'
                             r'static_cast<[^>]*>\s*\(\s*[\w.>-]*alloc|reinterpret_cast<[^>]*>\s*\(\s*[\w.>-]*alloc)' % re.escape(val), before) or \
                        re.search(r'\bnew\s*\(\s*%s\s*\)' % re.escape(val), before) or \
                        re.search(r'\b%s\s*->\s*\w+\s*(?:=(?!=)|\()' % re.escape(val), before):
                    info['publish'] = True
    # ---- plain accesses in the owner class and its derived classes
    by_parent = collections.defaultdict(list)
    for d, sp in defs:
        if d.get('parent_usr'):
            by_parent[d['parent_usr']].append((d, sp))
    rx_blank = re.compile(r'\b(?:%s|__atomic_\w+|__sync_\w+)\s*\(' % '|'.join(re.escape(n) for n in names_all))
    blanked_cache = {}

    def blanked(d, ob, cb):
        k = (d['file'], ob)
        if k not in blanked_cache:
            t = L.stripped(d['file'])[ob:cb + 1]
            chars = list(t)
            for mm in rx_blank.finditer(t):
                a, c = split_args(t, mm.end() - 1)
                if c is None:
                    continue
                for i in range(mm.start(), c + 1):
                    if chars[i] != '\n':
                        chars[i] = ' '
            blanked_cache[k] = ''.join(chars)
        return blanked_cache[k]

    rows = []
    derived_cache = {}
    for key, info in agg.items():
        rec = info['rec']
        kind = info['kind']
        if kind == 'field':
            owner = rec.get('parent_usr')
            od = db.definition(owner, astdb.RECORD_KINDS) if owner else None
            while od is not None and od.get('is_anonymous') and od.get('parent_usr'):
                od = db.definition(od['parent_usr'], astdb.RECORD_KINDS)
            owner_q = S.short_qname(od['qname']) if od else S.short_qname(rec.get('parent_qname', '?'))
            sym = '%s::%s' % (owner_q, rec['name'])
            cons = 'atomic-accessed field'
        elif kind == 'static':
            od = db.definition(rec.get('parent_usr'), astdb.RECORD_KINDS) if rec.get('parent_usr') else None
            sym = S.short_qname(rec['qname'])
            cons = 'atomic-accessed static member'
        else:
            od = None
            sym = S.short_qname(rec['qname'])
            cons = 'atomic-accessed global variable'
        t = S.L._clean(rec.get('canonical_type') or rec.get('type') or '')
        pre = ['macros=%s' % ','.join('%s:%d' % (k, v) for k, v in sorted(info['macros'].items())),
               'sites=%d' % len(info['sites']), 'type=%s' % t.replace('oceanbase::', '')]
        if t.endswith('*') or rec.get('canonical_kind') == 'pointer':
            pre.append('pointer-field')
        if 'volatile' in (rec.get('type') or '') or 'volatile' in t:
            pre.append('volatile')
        if od is not None and kind in ('field', 'static'):
            ou = od['usr']
            if ou not in derived_cache:
                derived_cache[ou] = [ou] + sorted(db.derived(ou))
            nm = rec['name']
            rx_use = re.compile(r'(?<![\w.>])(?:this\s*->\s*)?%s\b' % re.escape(nm))
            rx_w = re.compile(r'(?<![\w.>])(?:this\s*->\s*)?%s\b\s*(?:\[[^\]]*\]\s*)?(?:=(?!=)|[-+*/%%&|^]=|<<=|>>=|\+\+|--)|'
                              r'(?:\+\+|--)\s*(?:this\s*->\s*)?%s\b' % (re.escape(nm), re.escape(nm)))
            rx_addr = re.compile(r'&\s*\(?\s*(?:this\s*->\s*)?%s\b' % re.escape(nm))
            reads = writes = addrs = ctor = 0
            for cu in derived_cache[ou]:
                for d, (ob, cb) in by_parent.get(cu, ()):
                    t2 = blanked(d, ob, cb)
                    if nm not in t2:
                        continue
                    nu = len(rx_use.findall(t2))
                    nw = len(rx_w.findall(t2))
                    na = len(rx_addr.findall(t2))
                    if d['kind'] in ('ctor', 'dtor'):
                        ctor += nu
                        continue
                    writes += nw
                    addrs += na
                    reads += max(nu - nw - na, 0)
            pre.append('plain-reads=%d' % reads)
            pre.append('plain-writes=%d' % writes)
            if addrs:
                pre.append('addr-taken=%d' % addrs)
            if ctor:
                pre.append('ctor-dtor-uses=%d' % ctor)
        if info['publish']:
            pre.append('publish-pattern')
        if info['lazy']:
            pre.append('bcas-lazy-init')
        if info['flag']:
            pre.append('bcas-flag')
        if info['by_name']:
            pre.append('by-name')
        if info['in_macro']:
            pre.append('in-macro=%s' % ','.join(sorted(info['in_macro'])))
        un = S.unit_of(rec['file'])
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([rec['file'], sym, cons, rec['line'], '; '.join(pre)])
    atomic_field_usrs = {k for k, v in agg.items() if v['kind'] == 'field'}
    # ---- volatile fields never accessed atomically
    for f in db.by_kind('field'):
        if f.get('generated') or f['usr'] in atomic_field_usrs:
            continue
        if 'volatile' in (f.get('type') or '') or re.search(r'\bvolatile\b', f.get('canonical_type') or ''):
            if re.search(r'\bvolatile\b\s*[*&]', f.get('type') or '') and not re.search(r'\*\s*volatile', f.get('type') or ''):
                tag = 'pointer-to-volatile'
            else:
                tag = 'volatile-object'
            od = db.definition(f.get('parent_usr'), astdb.RECORD_KINDS) if f.get('parent_usr') else None
            while od is not None and od.get('is_anonymous') and od.get('parent_usr'):
                od = db.definition(od['parent_usr'], astdb.RECORD_KINDS)
            sym = '%s::%s' % (S.short_qname(od['qname']) if od else S.short_qname(f.get('parent_qname', '?')), f['name'])
            un = S.unit_of(f['file'])
            pre = [tag, 'type=%s' % S.L._clean(f.get('canonical_type') or '').replace('oceanbase::', '')]
            if un is not None and un.disposition != 'translate':
                pre.append('unit=%s' % un.disposition)
            rows.append([f['file'], sym, 'volatile field', f['line'], '; '.join(pre)])
    # ---- accessors returning a non-const scalar reference to a field
    seen = set()
    callers_rx_cache = {}
    for r in db.by_kind('method'):
        if r['usr'] in seen:
            continue
        seen.add(r['usr'])
        c = db.canonical(r['usr'])
        if c is None or c.get('generated'):
            continue
        rt = S.L._clean(c.get('canonical_return_type') or '')
        if not re.match(r'^(?:bool|char|signed char|unsigned char|short|unsigned short|int|unsigned int|long|unsigned long|'
                        r'long long|unsigned long long|volatile \w[\w ]*) &$', rt):
            continue
        ret_field = None
        for d in db.get(c['usr']):
            if d.get('is_definition') and not d.get('generated'):
                sp = S.body_span(d)
                if sp is None:
                    continue
                body = L.stripped(d['file'])[sp[0]:sp[1] + 1]
                m = re.search(r'\breturn\s+\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\)?\s*;', body)
                if m:
                    ret_field = m.group(1)
                break
        if ret_field is None:
            continue
        cu = c.get('parent_usr')
        f = env.fields(cu).get(ret_field) if cu and db.definition(cu, astdb.RECORD_KINDS) else None
        if f is None:
            continue
        od = db.definition(cu, astdb.RECORD_KINDS)
        sym = '%s::%s' % (S.short_qname(od['qname']), ret_field)
        nm = c['name']
        if nm not in callers_rx_cache:
            callers_rx_cache[nm] = count_calls(nm)
        pre = ['accessor=%s()' % nm, 'returns=%s' % rt, 'atomic-field' if f['usr'] in atomic_field_usrs else 'plain-field',
               'call-sites=%d' % callers_rx_cache[nm]]
        un = S.unit_of(c['file'])
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([c['file'], sym, 'scalar reference accessor', c['line'], '; '.join(pre)])
    for (p, sym), ent in regex_fields.items():
        pre = ['macros=%s' % ','.join('%s:%d' % (k, v) for k, v in sorted(ent['macros'].items())),
               'sites=%d' % len(ent['sites']), 'regex-fallback(tus=0)']
        un = S.unit_of(p)
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([p, sym, 'atomic-accessed field (regex; file not parsed)', ent['line'], '; '.join(pre)])
    for (p, label), ent in amb.items():
        pre = ['ambiguous:%s' % '|'.join(ent['cands'][:8]) + ('|+%d' % (len(ent['cands']) - 8) if len(ent['cands']) > 8 else ''),
               'macros=%s' % ','.join('%s:%d' % (k, v) for k, v in sorted(ent['macros'].items()))]
        rows.append([p, label, 'ambiguous atomic operand', ent['line'], '; '.join(pre)])
    for (p, label), ent in unres.items():
        pre = ['unresolved:%s' % ent['reason'],
               'macros=%s' % ','.join('%s:%d' % (k, v) for k, v in sorted(ent['macros'].items()))]
        un = S.unit_of(p)
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([p, label, 'unresolved atomic operand', ent['line'], '; '.join(pre)])
    seen_k = collections.Counter()
    for r in sorted(rows, key=lambda r: (r[0], r[3], r[1], r[2])):
        k = (r[0], r[1], r[2])
        seen_k[k] += 1
        if seen_k[k] > 1:
            r[1] = '%s#%d' % (r[1], seen_k[k])
    cnt = collections.Counter(r[2] for r in rows)
    L.finish(CAT, rows, ('%d ATOMIC_*/inc_update/dec_update/__atomic/__sync sites (ob_atomic.h macros) mapped to %d fields, '
                         '%d static members, %d globals; %d ambiguous and %d unresolved operand rows; %d volatile fields; '
                         '%d scalar-reference accessors' % (
                             len(sites), cnt['atomic-accessed field'], cnt['atomic-accessed static member'],
                             cnt['atomic-accessed global variable'], cnt['ambiguous atomic operand'],
                             cnt['unresolved atomic operand'], cnt['volatile field'], cnt['scalar reference accessor'])))


def count_calls(name):
    rx = re.compile(r'\b%s\s*\(\s*\)' % re.escape(name))
    n = 0
    for p in S.first_party_code():
        t = L.stripped(p)
        if name in t:
            n += len(rx.findall(t))
    return n


if __name__ == '__main__':
    main()
