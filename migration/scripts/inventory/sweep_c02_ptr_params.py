#!/usr/bin/env python3
"""c02-ptr-params: `T *&` out-parameters and allocating pointer returns (RULEBOOK §2.4, §5.2 #2).

Sites:
  A  every parameter of type `(const) T *&` (not `T *const &`) of a first-party function, one row per
     (function USR, parameter) on the canonical declaration (in-class/header declaration first; the
     out-of-line definition is the same row)                               -> 'T *& out-param'
  B  functions whose canonical return type is an object pointer and whose body returns the result of an
     allocation (directly, or through a local assigned from one)           -> 'pointer return (allocating)'
  C  files no TU parses (files.tsv tus = 0): regex over declarators with a `*&` parameter -> as A,
     pretag 'regex-only'

symbol: '<Class chain>::<function>[<specialization args>](<canonical parameter types>)[ const]#out:<i>' with i
the 0-based position in the parameter list (B: '#ret'); namespaces are dropped from the class chain and from the
parameter types (std:: kept); explicit specializations of function templates carry their template arguments;
identical symbols in one file (SFINAE overloads) get '#2', '#3', ... in line order.  Regex rows use the written
parameter types.

pretag (hint tags, then ';'-separated details), from the definition body (AST extent):
  callee-allocates:<allocator expr>  the parameter is assigned from alloc(/alloc_align(/OB_NEWx/OB_NEW/new (buf)/
                                     ob_malloc/op_alloc..., directly or through a local; <expr> = the receiver
                                     of .alloc(, the pool of OB_NEWx, or the function name
  callee-points-existing             assigned from a variable, member, &x, x[i], x->y, or an accessor call
                                     (get_*, at, top, front, back, ...)
  callee-assigns-call:<f>            assigned from another call whose source is not visible here
  forwards:<f>                       passed on as an argument to <f>, a function that itself has a T *& parameter
  passed-to:<f>                      (only when nothing above matched) passed as an argument to other calls
                                     (function pointers, templates, readers); at most 3 callees listed
  callee-advances                    ++/--/+=/-= on the pointer (a cursor into a buffer)
  callee-resets                      only ever assigned NULL/nullptr
  no-assignment                      a body exists but none of the above matches
  no-body                            no definition seen (pure virtual: also pure-virtual, overriders=N)
  macro-body                         the definition is produced by a macro (body not visible)
  unnamed                            the parameter has no name in the definition
  ir-slot, plan-node, module, allocator, const-pointee   from the pointee type (s3alib type sets)
  B rows: callee-allocates:<expr> as above; allocator-api when the function is a method of an allocator type
  or an alloc/realloc-like function returning void * / char * (allocator internals, mostly replace:mem).
  details: type=<parameter type>, body=<definition file:line>, returns=<type>, disp=<unit disposition when it
  is not translate>, overriders=<n> (pure virtual), macro=<name> (macro-made definition)
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c02-ptr-params'
RX_OB_NEWX = re.compile(r'\bOB_NEWx\s*\(')
RX_OB_NEW = re.compile(r'\b(OB_NEW\w*|ob_newx?|OB_NEWx\w+)\s*\(')
RX_FUNC_ALLOC = re.compile(r'\b(ob_malloc\w*|ob_realloc|op_alloc\w*|op_reclaim_alloc\w*|rp_alloc|sop_borrow|'
                           r'malloc|calloc|realloc|ob_alloc_\w+|alloc_\w*buf\w*)\s*\(')
RX_RECV_ALLOC = re.compile(r'((?:[A-Za-z_]\w*(?:\s*\([^()]*\))?(?:\s*\[[^\]]*\])?\s*(?:\.|->)\s*)*'
                           r'[A-Za-z_]\w*(?:\s*\([^()]*\))?)\s*(?:\.|->)\s*(alloc|alloc_align|alloc_aligned|allocate|'
                           r'alloc_buf|realloc)\s*\(')
RX_PLACEMENT = re.compile(r'\bnew\s*\(\s*([^()]*(?:\([^()]*\))?[^()]*)\)')
RX_PLAIN_NEW = re.compile(r'\bnew\s+(?:\(\s*std::nothrow\s*\)\s*)?[A-Za-z_]')
RX_NULL = re.compile(r'^\(?\s*(?:NULL|nullptr|0|0L|0UL)\s*\)?$')
RX_CAST = re.compile(r'^(?:static_cast|reinterpret_cast|const_cast|dynamic_cast)\s*<')
RX_CSTYLE = re.compile(r'^\(\s*(?:const\s+)?[A-Za-z_][\w:<>,\s]*\*+\s*(?:const\s*)?\)\s*')
ACCESSOR = re.compile(r'^(?:get\w*|at|top|front|back|head|tail|peek\w*|find\w*|first|last|next|prev|data|ptr|'
                      r'value|begin|end|operator\[\]|ref|obj|raw\w*|cur\w*|current\w*|this|ATOMIC_\w+|'
                      r'server_service|MTL|get_instance)$')
RX_BARE_ALLOC = re.compile(r'^(?:alloc|alloc_align|allocate|alloc_aligned)$')
RX_ALLOC_API = re.compile(r'^(?:alloc|realloc|alloc_align|allocate|alloc_aligned|malloc|calloc)\w*$')
STOP_CALLEES = frozenset('if while for switch return sizeof OB_ISNULL OB_NOT_NULL OB_UNLIKELY OB_LIKELY K KP KPC KR '
                         'UNUSED OB_ASSERT abort_unless static_cast reinterpret_cast const_cast dynamic_cast '
                         'OB_FAIL OB_SUCC OB_TMP_FAIL OZ OX OV CK'.split())


def is_out(p):
    return p.get('kind') == 'lvalue_ref' and p.get('pointee_kind') == 'pointer' and not p.get('pointee_const')


def rhs_at(text, i):
    """Expression text from i to the end of its statement / enclosing parenthesis."""
    depth = 0
    j = i
    n = len(text)
    while j < n:
        c = text[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                break
            depth -= 1
        elif c == ';' and depth == 0:
            break
        elif c == ',' and depth == 0:
            break
        j += 1
    return ' '.join(text[i:j].split())


def strip_casts(e):
    e = e.strip()
    for _ in range(6):
        while e.startswith('(') and S.matching(e, 0) == len(e) - 1:
            e = e[1:-1].strip()
        if RX_CAST.match(e):
            k = e.find('<')
            depth, j = 0, k
            while j < len(e):
                if e[j] == '<':
                    depth += 1
                elif e[j] == '>':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            rest = e[j + 1:].strip()
            if rest.startswith('('):
                e = rest[1:S.matching(rest, 0)].strip()
                continue
        m = RX_CSTYLE.match(e)
        if m:
            e = e[m.end():].strip()
            continue
        break
    return e


def norm_alloc(s):
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'^this->', '', s)
    s = s.replace(',', ' ')
    return s[:60]


def alloc_of(e):
    """Allocator expression if e allocates (OB_NEWx pool, receiver of .alloc(, function name), else None."""
    m = RX_OB_NEWX.search(e)
    if m:
        args = S.split_top(e[m.end():S.matching(e, m.end() - 1)])
        return 'OB_NEWx(%s)' % norm_alloc(args[1]) if len(args) > 1 else 'OB_NEWx'
    m = RX_OB_NEW.search(e)
    if m:
        return m.group(1)
    m = RX_RECV_ALLOC.search(e)
    if m:
        return norm_alloc(m.group(1))
    m = RX_FUNC_ALLOC.search(e)
    if m:
        return m.group(1)
    if RX_PLAIN_NEW.search(e):
        return 'new'
    return None


class Body(object):
    def __init__(self, text):
        self.text = text
        b = text.find('{')
        self.body = text[b:] if b >= 0 else ''
        self._assign = None

    def assignments(self, name):
        """RHS texts assigned to name ('name = rhs' not preceded by . -> * :: and declarations 'T *name = rhs')."""
        out = []
        for mm in re.finditer(r'\b%s\s*=(?!=)' % re.escape(name), self.body):
            k = mm.start() - 1
            while k >= 0 and self.body[k] in ' \t\n':
                k -= 1
            if k >= 0 and self.body[k] in '.>:':
                continue
            if k >= 0 and self.body[k] == '*':
                # 'T *name = rhs' (declaration) is fine; '*name = rhs' (deref) is not
                kk = k - 1
                while kk >= 0 and self.body[kk] in ' \t\n*':
                    kk -= 1
                if kk < 0 or not (self.body[kk].isalnum() or self.body[kk] in '_>'):
                    continue
            out.append(rhs_at(self.body, mm.end()))
        return out

    def classify_rhs(self, e, depth=0):
        """Set of tags for one assigned expression."""
        tags = set()
        e = e.strip()
        if not e:
            return tags
        if '?' in e and ':' in e:
            parts = S.split_top(e, '?')
            if len(parts) == 2:
                branches = S.split_top(parts[1], ':')
                if len(branches) == 2:
                    for b in branches:
                        tags |= self.classify_rhs(b, depth)
                    return tags
        a = alloc_of(e)
        if a:
            tags.add('callee-allocates:%s' % a)
            return tags
        mp = RX_PLACEMENT.search(e)
        core = strip_casts(e)
        if mp:
            buf = strip_casts(mp.group(1))
            a2 = self.local_alloc(buf, depth)
            tags.add('callee-allocates:%s' % (a2 or 'new(%s)' % norm_alloc(buf)))
            return tags
        if RX_NULL.match(core):
            tags.add('reset')
            return tags
        if re.match(r'^[A-Za-z_]\w*$', core):
            a2 = self.local_alloc(core, depth)
            if a2:
                tags.add('callee-allocates:%s' % a2)
            else:
                tags.add('callee-points-existing')
            return tags
        mc = re.search(r'([A-Za-z_]\w*)\s*(?:<[^()<>]*>)?\s*\($', core[:core.find('(') + 1]) if '(' in core else None
        if '(' in core and mc:
            fname = mc.group(1)
            if ACCESSOR.match(fname):
                tags.add('callee-points-existing')
            elif RX_BARE_ALLOC.match(fname):
                tags.add('callee-allocates:%s()' % fname)
            else:
                tags.add('callee-assigns-call:%s' % fname)
            return tags
        tags.add('callee-points-existing')
        return tags

    def local_alloc(self, name, depth):
        """Allocator when a local `name` is assigned from an allocation in the body."""
        if depth > 1 or not re.match(r'^[A-Za-z_]\w*$', name):
            return None
        for rhs in self.assignments(name):
            a = alloc_of(rhs)
            if a:
                return a
            mp = RX_PLACEMENT.search(rhs)
            if mp:
                return self.local_alloc(strip_casts(mp.group(1)), depth + 1) or 'new(%s)' % norm_alloc(mp.group(1))
            core = strip_casts(rhs)
            if re.match(r'^[A-Za-z_]\w*$', core) and core != name:
                a = self.local_alloc(core, depth + 1)
                if a:
                    return a
        return None

    def forwards(self, name, out_names):
        """(callees with a T *& parameter that receive the pointer, other callees that receive it)."""
        res, other = [], []
        for mm in re.finditer(r'(?<![\w.>])\b%s\b' % re.escape(name), self.body):
            i, j = mm.start(), mm.end()
            k = j
            while k < len(self.body) and self.body[k] in ' \t\n':
                k += 1
            if k >= len(self.body) or self.body[k] not in ',)':
                continue
            p = i - 1
            while p >= 0 and self.body[p] in ' \t\n':
                p -= 1
            if p < 0 or self.body[p] not in '(,':
                continue
            depth = 0
            q = p if self.body[p] == ',' else p
            q = i - 1
            while q >= 0:
                c = self.body[q]
                if c == ')':
                    depth += 1
                elif c == '(':
                    if depth == 0:
                        break
                    depth -= 1
                elif c in ';{}' and depth == 0:
                    q = -1
                    break
                q -= 1
            if q < 0:
                continue
            r = q - 1
            while r >= 0 and self.body[r] in ' \t\n':
                r -= 1
            if r >= 0 and self.body[r] == '>':
                d2 = 0
                while r >= 0:
                    if self.body[r] == '>':
                        d2 += 1
                    elif self.body[r] == '<':
                        d2 -= 1
                        if d2 == 0:
                            r -= 1
                            break
                    r -= 1
                while r >= 0 and self.body[r] in ' \t\n':
                    r -= 1
            e = r + 1
            while r >= 0 and (self.body[r].isalnum() or self.body[r] == '_'):
                r -= 1
            callee = self.body[r + 1:e]
            if not callee or callee in STOP_CALLEES or re.match(r'^(?:LOG_\w+|\w+_LOG\w*|K\w?)$', callee):
                continue
            if callee in out_names:
                res.append(callee)
            else:
                other.append(callee)
        return sorted(set(res)), sorted(set(other))

    def advances(self, name):
        v = re.escape(name)
        return bool(re.search(r'(?<![\w.>])\b%s\s*(?:\+\+|--|\+=|-=)|(?:\+\+|--)\s*%s\b' % (v, v), self.body))


def analyse(body, name, out_names):
    tags = []
    rhs_tags = set()
    for rhs in body.assignments(name):
        rhs_tags |= body.classify_rhs(rhs)
    allocs = sorted(t for t in rhs_tags if t.startswith('callee-allocates:'))
    tags.extend(allocs)
    if 'callee-points-existing' in rhs_tags:
        tags.append('callee-points-existing')
    tags.extend(sorted(t for t in rhs_tags if t.startswith('callee-assigns-call:')))
    fw, other = body.forwards(name, out_names)
    tags.extend('forwards:%s' % f for f in fw)
    if body.advances(name):
        tags.append('callee-advances')
    if not tags and rhs_tags == {'reset'}:
        tags.append('callee-resets')
    if not tags and other:
        tags.extend('passed-to:%s' % f for f in other[:3])
    if not tags:
        tags.append('no-assignment')
    return tags


def params_sig(m, rec):
    ps = []
    for p in rec.get('params', []):
        ps.append(m.strip_ns(p.get('canonical_type') or p.get('type', '')))
    s = '(%s)' % ', '.join(ps)
    if rec.get('is_const'):
        s += ' const'
    return s


def spec_args(m, rec):
    """'<args>' of an explicit function template specialization (record template_args, else the written
    declarator 'name<args>(' at the record's line), '' otherwise."""
    ta = rec.get('template_args')
    if ta:
        if isinstance(ta, str):
            ta = ta.strip()
            return m.strip_ns(ta if ta.startswith('<') else '<%s>' % ta)
        return '<%s>' % ', '.join(m.strip_ns(str(a)) for a in ta)
    if '<' in rec['name'] or not re.search(r'@F@%s<#' % re.escape(rec['name']), rec.get('usr', '')):
        return ''
    lines = L.stripped_lines(rec['file'])
    text = ' '.join(lines[rec['line'] - 1:rec['line'] + 3])
    mm = re.search(r'\b%s\s*<([^;{}()]*)>\s*\(' % re.escape(rec['name']), text)
    return '<%s>' % m.strip_ns(' '.join(mm.group(1).split())) if mm else ''


def fn_name(m, rec):
    parent = rec.get('parent_usr')
    base = rec['name'] + spec_args(m, rec)
    if parent in m.rec_any:
        return '%s::%s' % (m.chain(parent), base)
    return base


def regex_rows(out_names):
    """Fallback for files no TU parses: declarators with a `*&` parameter."""
    rows = []
    rx = re.compile(r'([~A-Za-z_][\w:]*)\s*\(([^;{}()]*\*\s*&[^;{}()]*)\)\s*(const)?\s*(?:override\s*)?'
                    r'(?:=\s*0\s*)?([;{:])')
    for p in S.unparsed_files():
        text = L.stripped(p)
        for mm in rx.finditer(text):
            name = mm.group(1)
            if name in ('if', 'while', 'for', 'switch', 'return', 'sizeof'):
                continue
            params = [x.strip() for x in S.split_top(mm.group(2)) if x.strip()]
            types = []
            outs = []
            for i, prm in enumerate(params):
                prm_t = re.sub(r'\s*=.*$', '', prm)
                mt = re.match(r'^(.*?)([A-Za-z_]\w*)?$', prm_t)
                t = (mt.group(1) if mt and mt.group(2) else prm_t).strip()
                pname = mt.group(2) if mt and mt.group(2) else ''
                types.append(' '.join(t.split()))
                if re.search(r'\*\s*&\s*$', t) and not re.search(r'\*\s*const\s*&\s*$', t):
                    outs.append((i, pname))
            if not outs:
                continue
            cls = L.enclosing_class(p, mm.start())
            qual = name if '::' in name or not cls else '%s::%s' % (cls, name)
            sig = '%s(%s)%s' % (qual, ', '.join(types), ' const' if mm.group(3) else '')
            body = None
            if mm.group(4) in '{:':
                b = text.find('{', mm.end() - 1)
                if b >= 0:
                    body = Body(text[mm.start():S.matching(text, b, '{', '}') + 1])
            for i, pname in outs:
                tags = ['regex-only']
                if body is None:
                    tags.append('no-body')
                elif not pname:
                    tags.append('unnamed')
                else:
                    tags.extend(analyse(body, pname, out_names))
                rows.append((p, '%s#out:%d' % (sig, i), 'T *& out-param', L.line_of(p, mm.start()),
                             S.fmt_tags(tags)))
    return rows


def pointee_tags(m, ctype):
    """Hints from the pointee of a `T *&` parameter type."""
    pt = re.sub(r'\s*\*\s*(?:const\s*)?&\s*$', '', ctype or '').strip()
    tags = []
    u = m.resolve(re.sub(r'^(?:const\s+|volatile\s+)+', '', pt))
    if u in m.ir:
        tags.append('ir-slot')
    if u in m.plan_nodes:
        tags.append('plan-node')
    if u in m.modules:
        tags.append('module')
    if u in m.allocators:
        tags.append('allocator')
    if pt.startswith('const '):
        tags.append('const-pointee')
    return tags


def main():
    m = S.Model(funcs=True)
    m.add_service_modules()
    recs = collections.defaultdict(list)
    for r in m.db.records:
        if r['kind'] in S.FK and not r.get('generated'):
            recs[r['usr']].append(r)
    out_usrs = sorted(u for u, rs in recs.items() if any(is_out(p) for r in rs for p in r.get('params', [])))
    out_names = set(recs[u][0]['name'] for u in out_usrs)
    overriders = collections.Counter()
    for u, rs in recs.items():
        c = rs[0]
        for o in c.get('overrides', []) or []:
            overriders[o] += 1
    rows = []
    for u in out_usrs:
        c = m.db.canonical(u)
        if c is None or c.get('generated'):
            continue
        d = m.func_def(u)
        sig = '%s%s' % (fn_name(m, c), params_sig(m, c))
        body = None
        if d is not None and not d.get('in_macro'):
            body = Body(m.body(d))
        for i, p in enumerate(c.get('params', [])):
            if not is_out(p):
                continue
            tags, det = [], []
            if d is None:
                tags.append('no-body')
                if c.get('is_pure'):
                    tags.append('pure-virtual')
                    det.append('overriders=%d' % overriders[u])
            elif d.get('in_macro'):
                tags.append('macro-body')
                det.append('macro=%s' % d.get('macro', ''))
            else:
                dp = d.get('params', [])
                pname = dp[i].get('name', '') if i < len(dp) else ''
                if not pname:
                    tags.append('unnamed')
                else:
                    tags.extend(analyse(body, pname, out_names))
                det.append('body=%s:%d' % (d['file'], d['line']))
            tags.extend(pointee_tags(m, p.get('canonical_type', '')))
            det.insert(0, 'type=%s' % m.strip_ns(p.get('canonical_type', '')))
            det.append(m.disp_detail(c['file']))
            rows.append((c['file'], '%s#out:%d' % (sig, i), 'T *& out-param', c['line'], S.fmt_tags(tags, det)))
    # B: allocating pointer returns
    nb = 0
    ret_usrs = sorted(u for u, rs in recs.items() if rs[0].get('return_kind') == 'pointer' or
                      (m.db.canonical(u) or rs[0]).get('return_kind') == 'pointer')
    for u in ret_usrs:
        c = m.db.canonical(u)
        if c is None or c.get('generated'):
            continue
        d = m.func_def(u)
        if d is None or d.get('in_macro'):
            continue
        body = Body(m.body(d))
        allocs = set()
        for mm in re.finditer(r'\breturn\b\s*', body.body):
            e = rhs_at(body.body, mm.end())
            if not e:
                continue
            for t in body.classify_rhs(e):
                if t.startswith('callee-allocates:'):
                    allocs.add(t)
        if not allocs:
            continue
        sig = '%s%s' % (fn_name(m, c), params_sig(m, c))
        det = ['returns=%s' % m.strip_ns(c.get('canonical_return_type') or c.get('return_type', '')),
               'body=%s:%d' % (d['file'], d['line']), m.disp_detail(c['file'])]
        tags = sorted(allocs)
        parent = c.get('parent_usr')
        if parent in m.allocators or (RX_ALLOC_API.match(c['name']) and
                                      re.match(r'^(?:const )?(?:void|char) \*$', c.get('canonical_return_type', ''))):
            tags.append('allocator-api')
        rows.append((c['file'], '%s#ret' % sig, 'pointer return (allocating)', c['line'],
                     S.fmt_tags(tags, det)))
        nb += 1
    rr = regex_rows(out_names)
    rows.extend(rr)
    # unique keys: identical symbols in one file (SFINAE overloads that differ only in their template
    # conditions) keep the symbol for the first in line order and get '#2', '#3', ... after it
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    order = sorted(range(len(rows)), key=lambda i: (rows[i][0], rows[i][3], rows[i][1], rows[i][4]))
    seen = collections.Counter()
    fixed = list(rows)
    ndup = 0
    for i in order:
        r = rows[i]
        k = (r[0], r[1], r[2])
        if cnt[k] > 1:
            seen[k] += 1
            if seen[k] > 1:
                fixed[i] = (r[0], '%s#%d' % (r[1], seen[k]), r[2], r[3], r[4])
                ndup += 1
    rows = fixed
    by = collections.Counter(r[2] for r in rows)
    tagc = collections.Counter()
    for r in rows:
        if r[2] != 'T *& out-param':
            continue
        for t in S.tags_of(r[4]):
            tagc[t.split(':', 1)[0]] += 1
    note = ('A T *& out-params %d = %d AST (one per function USR and parameter) + %d regex-only (files no TU parses); '
            'B allocating pointer returns %d; %d symbols with a #N ordinal (SFINAE overloads); A pretags %s') % (
        by['T *& out-param'], by['T *& out-param'] - len(rr), len(rr), nb, ndup,
        ' '.join('%s=%d' % kv for kv in sorted(tagc.items())))
    n = L.finish(CAT, rows, note)
    for k, v in sorted(by.items()):
        print('  %-30s %d' % (k, v))
    for k, v in sorted(tagc.items()):
        print('  tag %-26s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
