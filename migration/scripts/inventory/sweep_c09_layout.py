#!/usr/bin/env python3
"""c09-layout: unions, bit-field records, packed records, flexible array members and reinterpret_cast sites
(RULEBOOK 0.9 头加尾随数据, 0.10 落盘头 / packed, §2.12 联合体 / 位域, §5.2 #9).

Records come from the astdb definitions (generated files excluded): every union (`union`), every record
with a bit-field (`bit-field record`, one row per record), every packed record (`packed record`, attribute
or #pragma pack), every flexible/zero-length array member (`flexible array member`, one row per field).
reinterpret_cast sites are a regex over the comment/string-stripped first-party sources (`reinterpret_cast`),
symbol `<enclosing function>#reinterpret_cast<T>` (#2, #3, … for repeats in one file, line order).
Files no TU parses get regex rows for unions, bit-fields, packed attributes and zero-length arrays.

Pretags (hints):
  records   `scalar-only` / `has-pointer` / `dependent` (a member, recursively through member records and
            arrays, is a pointer, reference, function or member pointer; `dependent` = template parameter),
            `size=`, `bitfields=<n>`, `bits=<sum>`, `units=<storage types>`, `in-union`, `packed_by=`,
            `pragma_pack=`, `elem=` (flexible member element type), and the on-disk heuristics
            `on-disk?:serialize` (the record or its enclosing named record has serialize/deserialize/
            get_serialize_size methods), `on-disk?:memcpy` (`sizeof(Name)` inside a MEMCPY/memcpy/MEMMOVE/
            memmove call anywhere), `on-disk?:buffer-view` (`reinterpret_cast<Name *>` of a buffer anywhere)
  casts     `byte-read` / `byte-write` (a scalar pointer cast that is dereferenced: `*cast(...)`,
            `cast(...)[i]`, read or assigned), `pod-view` (a record pointer cast of a byte buffer, or an
            object viewed as bytes), `addr-as-key` (pointer to integer), `base-derived-slot`
            (`T *&` / `T **` targets: a slot of one pointer type used as another), `fnptr` (function pointer
            target or operand), `other`; plus `target=<T>`
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
CAT = 'c09-layout'
SCALAR = (r'(?:u?int(?:8|16|32|64|128)_t|(?:unsigned\s+|signed\s+)?(?:char|short|int|long\s+long|long)|unsigned|'
          r'size_t|ssize_t|intptr_t|uintptr_t|ptrdiff_t|float|double|bool|wchar_t|char16_t|char32_t|__m128i|__m128|'
          r'__m256i|__m256|__m512i|__int128|uint128_t|int128_t|obstr_size_t|uchar|uint|ulong|ObWideInteger<[^<>]*>|'
          r'int512_t|int256_t|uint256_t)')
RX_SCALAR = re.compile(r'^%s$' % SCALAR)
BUFFERISH = re.compile(r'(?i)(?:buf|data|char|byte|raw|payload|ptr\s*\(\s*\)|\bpos\b|offset|addr|mem|block|base|'
                       r'start|cur|\+\s*\w)')


def rec_symbol(db, r):
    if not r.get('is_anonymous'):
        return S.short_qname(r['qname'])
    parent = db.definition(r.get('parent_usr'), astdb.RECORD_KINDS) if r.get('parent_usr') else None
    pq = rec_symbol(db, parent) if parent is not None else S.short_qname(r.get('parent_qname') or '')
    return '%s::(anonymous %s@%d:%d)' % (pq, r['kind'], r['line'], r.get('col', 0))


def named_owner(db, r):
    while r is not None and r.get('is_anonymous') and r.get('parent_usr'):
        r = db.definition(r['parent_usr'], astdb.RECORD_KINDS)
    return r


class PointerScan(object):
    def __init__(self, db, env):
        self.db, self.env, self.memo = db, env, {}

    def has_pointer(self, usr, depth=0):
        """'has-pointer' / 'scalar-only' / 'dependent'."""
        if usr in self.memo:
            return self.memo[usr]
        if depth > 12:
            return 'scalar-only'
        self.memo[usr] = 'scalar-only'
        res = 'scalar-only'
        for f in self.db.fields(usr, inherited=True):
            k = self.field_kind(f, depth)
            if k == 'has-pointer':
                res = 'has-pointer'
                break
            if k == 'dependent':
                res = 'dependent'
        self.memo[usr] = res
        return res

    def field_kind(self, f, depth):
        ck = f.get('canonical_kind') or f.get('type_kind')
        ct = f.get('canonical_type') or ''
        if ck in ('pointer', 'function_pointer', 'member_pointer', 'lvalue_ref', 'rvalue_ref'):
            return 'has-pointer'
        if ck in ('template_param', 'dependent'):
            return 'dependent'
        if ck in ('constant_array', 'array', 'incomplete_array'):
            et = f.get('element_type') or re.sub(r'\[[^\]]*\]', '', ct)
            if '*' in et or '(' in et:
                return 'has-pointer'
            if 'type-parameter' in et:
                return 'dependent'
            ru = self.env.record_of_type(et)
            return self.has_pointer(ru, depth + 1) if ru else 'scalar-only'
        if ck in ('record', 'template_spec'):
            if 'type-parameter' in ct:
                return 'dependent'
            ru = self.env.record_of_type(ct)
            return self.has_pointer(ru, depth + 1) if ru else 'scalar-only'
        return 'scalar-only'


def disk_hints(db, names_memcpy, names_view, r):
    owner = named_owner(db, r)
    out = []
    if owner is None:
        return out
    meths = {m['name'] for m in db.methods(owner['usr'])}
    if meths & {'serialize', 'deserialize', 'serialize_', 'deserialize_', 'get_serialize_size'}:
        out.append('on-disk?:serialize')
    if owner['name'] in names_memcpy:
        out.append('on-disk?:memcpy')
    if owner['name'] in names_view:
        out.append('on-disk?:buffer-view')
    return out


def scan_text_hints():
    memcpy_names, view_names = set(), set()
    rx_mc = re.compile(r'\b(?:MEMCPY|memcpy|MEMMOVE|memmove|std::memcpy)\s*\(')
    rx_sz = re.compile(r'\bsizeof\s*\(\s*(?:const\s+)?(?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)\s*\)')
    rx_view = re.compile(r'\breinterpret_cast\s*<\s*(?:const\s+)?(?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)\s*\*\s*>\s*\(([^;]*)')
    for p in S.first_party_code():
        t = L.stripped(p)
        if 'memcpy' in t.lower() or 'memmove' in t.lower():
            for m in rx_mc.finditer(t):
                seg = t[m.end():m.end() + 400]
                d, j = 1, 0
                while j < len(seg) and d:
                    if seg[j] == '(':
                        d += 1
                    elif seg[j] == ')':
                        d -= 1
                    j += 1
                for mm in rx_sz.finditer(seg[:j]):
                    memcpy_names.add(mm.group(1))
        if 'reinterpret_cast' in t:
            for m in rx_view.finditer(t):
                if BUFFERISH.search(m.group(2)[:120]):
                    view_names.add(m.group(1))
    return memcpy_names, view_names


def classify_cast(text, start, target, args_end, operand, fnptr_typedefs):
    t = ' '.join(target.split())
    tn = re.sub(r'\b(?:const|volatile)\b', '', t)
    tn = ' '.join(tn.split())
    before = text[max(0, start - 3):start].rstrip()
    after = text[args_end + 1:args_end + 40].lstrip()
    deref = before.endswith('*') or before.endswith('*(') or after.startswith('[')
    member = after.startswith('->')
    if re.search(r'\*\s*&$', tn) or re.search(r'\*\s*\*$', tn):
        return 'base-derived-slot'
    if '(*' in tn or '(&' in tn or tn.rstrip('* ') in fnptr_typedefs or re.match(r'^\s*&?\s*[A-Za-z_][\w:]*\s*$', operand or '') \
            and re.search(r'(?i)func|_fn\b|handler|callback|hook', operand or ''):
        return 'fnptr'
    base = tn.rstrip('*& ').strip()
    is_ptr = tn.endswith('*')
    is_ref = tn.endswith('&')
    if not is_ptr and not is_ref and RX_SCALAR.match(base):
        return 'addr-as-key'
    if is_ptr and RX_SCALAR.match(base):
        if deref:
            # a write when the dereferenced cast is assigned
            k = args_end + 1
            tail = text[k:k + 200]
            if after.startswith('['):
                mm = re.match(r'\s*\[[^\]]*\]\s*', tail)
                tail = tail[mm.end():] if mm else tail
            if re.match(r'\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)', tail) or re.search(r'(?:\+\+|--)\s*$', text[max(0, start - 4):start]):
                return 'byte-write'
            return 'byte-read'
        return 'pod-view'
    if (is_ptr or is_ref) and base not in ('void',):
        if member or BUFFERISH.search(operand or ''):
            return 'pod-view'
    if is_ptr and base == 'void':
        return 'other'
    return 'other'


def main():
    db = S.load_db(astdb.RECORD_KINDS + astdb.FUNC_KINDS + ('field', 'typedef', 'alias'))
    env = S.TypeEnv(db)
    ps = PointerScan(db, env)
    memcpy_names, view_names = scan_text_hints()
    rows = []
    recs = [r for r in db.by_kind(*astdb.RECORD_KINDS) if r.get('is_definition') and not r.get('generated')]
    # ---- unions
    for r in recs:
        if r['kind'] != 'union':
            continue
        pre = [ps.has_pointer(r['usr'])]
        if r.get('size') is not None:
            pre.append('size=%s' % r['size'])
        pre.append('members=%s' % r.get('n_fields', 0))
        pre += disk_hints(db, memcpy_names, view_names, r)
        add_unit(pre, r['file'])
        rows.append([r['file'], rec_symbol(db, r), 'union', r['line'], '; '.join(pre)])
    # ---- bit-field records
    bf = collections.defaultdict(list)
    for f in db.by_kind('field'):
        if not f.get('generated') and f.get('bitwidth') is not None:
            bf[f['parent_usr']].append(f)
    for pu, fs in bf.items():
        r = db.definition(pu, astdb.RECORD_KINDS)
        if r is None or r.get('generated'):
            continue
        units = sorted({S.L._clean(f.get('canonical_type') or '').replace('oceanbase::', '') for f in fs})
        pre = ['bitfields=%d' % len(fs), 'bits=%d' % sum(int(f['bitwidth']) for f in fs),
               'units=%s' % ','.join(units)]
        if r.get('size') is not None:
            pre.append('size=%s' % r['size'])
        parent = db.definition(r.get('parent_usr'), astdb.RECORD_KINDS) if r.get('parent_usr') else None
        if r['kind'] == 'union' or (parent is not None and parent['kind'] == 'union'):
            pre.append('in-union')
        pre += disk_hints(db, memcpy_names, view_names, r)
        add_unit(pre, r['file'])
        rows.append([r['file'], rec_symbol(db, r), 'bit-field record', r['line'], '; '.join(pre)])
    # ---- packed records
    for r in recs:
        if not r.get('packed'):
            continue
        pre = ['packed_by=%s' % r.get('packed_by', '?')]
        if r.get('pragma_pack'):
            pre.append('pragma_pack=%s' % r['pragma_pack'])
        if r.get('size') is not None:
            pre.append('size=%s' % r['size'])
        pre.append(ps.has_pointer(r['usr']))
        pre += disk_hints(db, memcpy_names, view_names, r)
        add_unit(pre, r['file'])
        rows.append([r['file'], rec_symbol(db, r), 'packed record', r['line'], '; '.join(pre)])
    # ---- flexible array members
    for f in db.by_kind('field'):
        if f.get('generated') or not f.get('is_flexible_array'):
            continue
        r = db.definition(f['parent_usr'], astdb.RECORD_KINDS)
        if r is None:
            continue
        et = S.L._clean(f.get('element_type') or re.sub(r'\[[^\]]*\]', '', f.get('canonical_type') or ''))
        pre = ['elem=%s' % et.replace('oceanbase::', ''), 'decl=%s' % S.L._clean(f.get('type') or '')]
        if r.get('size') is not None:
            pre.append('record-size=%s' % r['size'])
        if r['kind'] == 'union':
            pre.append('in-union')
        pre += disk_hints(db, memcpy_names, view_names, r)
        add_unit(pre, f['file'])
        rows.append([f['file'], '%s::%s' % (rec_symbol(db, r), f['name']), 'flexible array member', f['line'],
                     '; '.join(pre)])
    # ---- reinterpret_cast sites
    fnptr_typedefs = {S.L._clean(t['name']) for t in db.by_kind('typedef', 'alias')
                      if t.get('canonical_kind') == 'function_pointer' or (t.get('canonical_kind') == 'pointer' and
                                                                           '(*)' in (t.get('canonical_type') or ''))}
    rx_rc = re.compile(r'\breinterpret_cast\s*<')
    cast_rows = []
    for p in S.first_party_code():
        text = L.stripped(p)
        if 'reinterpret_cast' not in text:
            continue
        for m in rx_rc.finditer(text):
            i = m.end() - 1
            d = 0
            j = i
            while j < len(text):
                ch = text[j]
                if ch == '<':
                    d += 1
                elif ch == '>':
                    d -= 1
                    if d == 0:
                        break
                elif ch in ';{}':
                    break
                j += 1
            target = text[i + 1:j] if j < len(text) and text[j] == '>' else '?'
            k = j + 1
            while k < len(text) and text[k] in ' \t\n':
                k += 1
            operand, close = '', k
            if k < len(text) and text[k] == '(':
                dd, q = 0, k
                while q < len(text):
                    if text[q] == '(':
                        dd += 1
                    elif text[q] == ')':
                        dd -= 1
                        if dd == 0:
                            break
                    q += 1
                operand = ' '.join(text[k + 1:q].split())
                close = q
            kind = classify_cast(text, m.start(), target, close, operand, fnptr_typedefs)
            owner = L.site_owner(p, m.start())
            tnorm = ' '.join(target.split())
            sym = '%s#reinterpret_cast<%s>' % (owner, tnorm)
            pre = [kind, 'target=%s' % tnorm]
            if operand:
                pre.append('operand=%s' % operand[:80])
            add_unit(pre, p)
            cast_rows.append([p, sym, 'reinterpret_cast', L.line_of(p, m.start()), '; '.join(pre)])
    seen = collections.Counter()
    for r in sorted(cast_rows, key=lambda r: (r[0], r[3])):
        k = (r[0], r[1])
        seen[k] += 1
        if seen[k] > 1:
            r[1] = '%s#%d' % (r[1], seen[k])
    rows += cast_rows
    rows += fallback_rows()
    seen = collections.Counter()
    for r in sorted(rows, key=lambda r: (r[0], r[3], r[1], r[2])):
        k = (r[0], r[1], r[2])
        seen[k] += 1
        if seen[k] > 1:
            r[1] = '%s#%d' % (r[1], seen[k])
    c = collections.Counter(r[2] for r in rows)
    kinds = collections.Counter(r[4].split(';')[0] for r in cast_rows)
    L.finish(CAT, rows, ('unions %d + bit-field records %d + packed records %d + flexible array members %d (astdb) + '
                         'reinterpret_cast sites %d (regex: %s) + %d regex rows for files no TU parses') % (
        c['union'], c['bit-field record'], c['packed record'], c['flexible array member'], c['reinterpret_cast'],
        ', '.join('%s %d' % kv for kv in sorted(kinds.items())), sum(v for k, v in c.items() if 'regex' in k)))


def add_unit(pre, path):
    un = S.unit_of(path)
    if un is not None and un.disposition != 'translate':
        pre.append('unit=%s' % un.disposition)


RX_FB_UNION = re.compile(r'\bunion\s*([A-Za-z_]\w*)?\s*\{')
RX_FB_BITFIELD = re.compile(r'(?m)^[ \t]*(?:[A-Za-z_][\w:<>]*[ \t]+)+([A-Za-z_]\w*)[ \t]*:[ \t]*(\d+)[ \t]*;')
RX_FB_PACKED = re.compile(r'__attribute__\s*\(\(\s*(?:__)?packed')
RX_FB_FLEX = re.compile(r'(?m)^[ \t]*(?:[A-Za-z_][\w:<>]*[ \t*]+)+([A-Za-z_]\w*)[ \t]*\[[ \t]*0?[ \t]*\][ \t]*;')


def fallback_rows():
    rows = []
    for p in S.tus0_files():
        text = L.stripped(p)
        st = L.structural_text(p)
        for m in RX_FB_UNION.finditer(text):
            if st[m.start():m.end()].strip() == '':
                continue
            cls = L.enclosing_class(p, m.start()) or ''
            nm = m.group(1) or '(anonymous union@%d)' % L.line_of(p, m.start())
            rows.append([p, ('%s::%s' % (cls, nm)) if cls else nm, 'union (regex; file not parsed)',
                         L.line_of(p, m.start()), 'regex-fallback(tus=0)'])
        per_cls = collections.OrderedDict()
        for m in RX_FB_BITFIELD.finditer(text):
            cls = L.enclosing_class(p, m.start(1))
            if not cls:
                continue
            ent = per_cls.setdefault(cls, [L.line_of(p, m.start(1)), 0, 0])
            ent[1] += 1
            ent[2] += int(m.group(2))
        for cls, (line, n, bits) in per_cls.items():
            rows.append([p, cls, 'bit-field record (regex; file not parsed)', line,
                         'bitfields=%d; bits=%d; regex-fallback(tus=0)' % (n, bits)])
        for m in RX_FB_PACKED.finditer(text):
            cls = L.enclosing_class(p, m.start()) or L.site_owner(p, m.start())
            rows.append([p, '%s#packed' % cls, 'packed record (regex; file not parsed)', L.line_of(p, m.start()),
                         'regex-fallback(tus=0)'])
        for m in RX_FB_FLEX.finditer(text):
            cls = L.enclosing_class(p, m.start(1))
            if not cls:
                continue
            rows.append([p, '%s::%s' % (cls, m.group(1)), 'flexible array member (regex; file not parsed)',
                         L.line_of(p, m.start(1)), 'regex-fallback(tus=0)'])
    return rows


if __name__ == '__main__':
    main()
