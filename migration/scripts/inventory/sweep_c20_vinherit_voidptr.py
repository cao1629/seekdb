#!/usr/bin/env python3
"""c20-vinherit-voidptr: virtual inheritance and `void *` data members (RULEBOOK §2.13 多重继承,
0.4 跨层 pimpl, §5.2 #20).

Sites:
  'virtual base specifier'  every virtual base specifier of a first-party record definition (AST: 2)
  'void * member'           every data member whose type is (cv) void * or an array of void * (AST 186 + arrays),
                            regex fallback for files no TU parses (pretag regex-only)
void * members are also c03 rows (pretag void-pointee); this category decides them.

symbol: '<Derived>#virtual:<Base>' for specifiers; '<Class chain>::<field>' for members.

pretag for void * members (first matching rule; evidence from a scan of every first-party file for
static_cast/reinterpret_cast/const_cast<T *>(operand) and C-style (T *)operand whose operand ends in the
member name: 'x.m', 'x->m', or a bare 'm' inside a function of the owning class or a file of its unit):
  pimpl          a cast to a record type of a crate the owner's crate cannot depend on, or a cast site in such a
                 crate (RULEBOOK 0.3 crate graph; the 0.4 cross-layer pimpl: ObTxExecResult::impl_)
  buffer         every cast seen is to a byte/scalar pointer (char, uint8_t, int64_t, float, ...); or, with no
                 cast seen, the name looks like a buffer (buf, mem, data, ptr, addr, base, block, page, chunk,
                 start, end, area, payload, raw) and not like a handle (user_data, ctx, arg, handle, impl, ...)
  opaque-handle  every other member (casts to record types of the owner's crate, callback user data, library
                 handles such as CURL *, iterator/context handles)
details: casts=<n>, to=<types> (at most 4), cast-in=<crate>:<file>:<line> (first foreign cast), disp=<...>.
Virtual base rows: siblings=<other records with the same virtual base>, diamond=<records deriving from two of
them>.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c20-vinherit-voidptr'
RX_CXX_CAST = re.compile(r'\b(?:static_cast|reinterpret_cast|const_cast)\s*<\s*([^;{}<>]*(?:<[^;{}]*?>)?[^;{}<>]*?)'
                         r'\s*>\s*\(')
RX_C_CAST = re.compile(r'\(\s*((?:const\s+)?(?:struct\s+)?[A-Za-z_][\w:]*(?:\s*<[^()<>;]*>)?\s*\*+\s*(?:const\s*)?)\)'
                       r'\s*\(?\s*((?:[A-Za-z_]\w*(?:\s*\[[^\]]*\])?\s*(?:\.|->)\s*)*[A-Za-z_]\w*)')
RX_LAST = re.compile(r'(?:^|\.|->|::)\s*([A-Za-z_]\w*)\s*$')
BYTE_T = re.compile(r'^(?:const\s+)?(?:unsigned\s+char|signed\s+char|char|u?int8_t|uint8|int8|u?int(?:16|32|64)_t|'
                    r'long|long\s+long|unsigned(?:\s+long)*|int|uint\d+|int\d+|float|double|__m128i)\s*\*+$')
OPAQUE_NAME = re.compile(r'(user_data|ctx|context|arg|handle|impl|cookie|param|owner|host|session|conn|thread|tid|'
                         r'cb|callback|func|fn|iter|key|val)', re.I)
BUF_NAME = re.compile(r'(buf|mem|data|ptr|addr|base|block|page|chunk|start|end|area|payload|raw)', re.I)


def scan_casts(void_names):
    """{name: [(file, line, target type text, operand text, enclosing offset)]} for casts of operands ending in name."""
    out = collections.defaultdict(list)
    for p in L.first_party():
        raw = L.raw(p)
        if 'cast' not in raw and '*)' not in raw and '* )' not in raw:
            continue
        text = L.stripped(p)
        for mm in RX_CXX_CAST.finditer(text):
            t = ' '.join(mm.group(1).split())
            if '*' not in t:
                continue
            o = mm.end() - 1
            e = S.matching(text, o)
            operand = ' '.join(text[o + 1:e].split())
            ml = RX_LAST.search(operand)
            if ml and ml.group(1) in void_names:
                out[ml.group(1)].append((p, L.line_of(p, mm.start()), t, operand))
        if '*)' in raw or '* )' in raw:
            for mm in RX_C_CAST.finditer(text):
                t = ' '.join(mm.group(1).split())
                operand = ' '.join(mm.group(2).split())
                ml = RX_LAST.search(operand)
                if ml and ml.group(1) in void_names:
                    out[ml.group(1)].append((p, L.line_of(p, mm.start()), t, operand))
    return out


def main():
    m = S.Model(funcs=True)
    rows = []
    # ---------------------------------------------------------------- virtual bases
    vb = collections.defaultdict(list)
    for u in sorted(m.defs):
        r = m.defs[u]
        if r.get('generated'):
            continue
        for b in r.get('bases', []):
            if b.get('virtual'):
                vb[m.resolve_base(b) or b.get('qname')].append(u)
    for base, ders in sorted(vb.items(), key=lambda kv: kv[0] or ''):
        dset = set(ders)
        diamonds = sorted(set(m.chain(x) for x in m.defs
                              if len(dset & set(m.ancestors(x))) >= 2))
        bname = m.chain(base) if base in m.defs else m.strip_ns(base or '')
        for u in ders:
            r = m.defs[u]
            sib = sorted(m.chain(x) for x in ders if x != u)
            det = ['siblings=%s' % ('+'.join(sib) if sib else '-'), 'diamond=%s' % ('+'.join(diamonds) or '-'),
                   m.disp_detail(r['file'])]
            rows.append([r['file'], '%s#virtual:%s' % (m.chain(u), bname), 'virtual base specifier', r['line'],
                         S.fmt_tags([], det), r['qname']])
    # ---------------------------------------------------------------- void * members
    vf = []
    for f in m.db.records:
        if f['kind'] != 'field' or f.get('generated'):
            continue
        ck = f.get('canonical_kind')
        if ck == 'pointer' and f.get('pointee_kind') == 'builtin' and f.get('pointee_qname') == 'void':
            vf.append(f)
        elif ck in ('constant_array', 'array', 'incomplete_array') and f.get('element_kind') == 'pointer' and \
                re.match(r'^(?:const\s+)?(?:volatile\s+)?void\s*\*(?:\s*const)?$', (f.get('element_type') or '').strip()):
            vf.append(f)
    vf.sort(key=lambda r: (r['file'], r['line'], r.get('col', 0), r['usr']))
    names = set(f['name'] for f in vf)
    casts = scan_casts(names)
    for f in vf:
        owner = m.owner_of_field(f)
        orec = m.defs.get(owner) or m.rec_any.get(owner) or {}
        ocrate = m.crate(f['file'])
        unit = m.unit(f['file'])
        unit_files = set(unit['members'].split(',')) if unit else {f['file']}
        oname = [c for c in m.chain(owner).split('::') if not c.startswith('anon@')]
        oname = re.sub(r'<.*', '', oname[-1]) if oname else ''
        sites = []
        for (p, line, t, operand) in casts.get(f['name'], []):
            qualified = bool(re.search(r'(\.|->)\s*%s\s*$' % re.escape(f['name']), operand)) and \
                not operand.startswith('this')
            if qualified:
                if oname and oname not in L.raw(p):
                    continue
            else:
                enc = m.db.enclosing(p, line, kinds=S.FK)
                if not ((enc is not None and enc.get('parent_usr') == owner) or p in unit_files):
                    continue
            sites.append((p, line, t, operand))
        tags, det = [], []
        foreign = []
        to = []
        byte = False
        typed = False
        for (p, line, t, operand) in sites:
            tt = re.sub(r'\s*\*+\s*(?:const\s*)?$', '', t).strip()
            tt = re.sub(r'^(?:const\s+|struct\s+)+', '', tt)
            to.append(m.strip_ns(tt))
            if BYTE_T.match(t.strip()):
                byte = True
                continue
            typed = True
            cands = m.resolve_written(tt)
            tcr = m.crate(m.defs[cands[0]]['file']) if cands else ''
            scr = m.crate(p)
            if (tcr and not S.crate_sees(ocrate, tcr)) or (scr and not S.crate_sees(ocrate, scr)):
                foreign.append((scr or tcr, p, line))
        if foreign:
            tags.append('pimpl')
            fc = sorted(foreign)[0]
            det.append('cast-in=%s:%s:%d' % fc)
        elif byte and not typed:
            tags.append('buffer')
        elif sites:
            tags.append('opaque-handle')
        elif BUF_NAME.search(f['name']) and not OPAQUE_NAME.search(f['name']):
            tags.append('buffer')
        else:
            tags.append('opaque-handle')
        det.insert(0, 'casts=%d' % len(sites))
        if to:
            det.append('to=%s' % ' '.join(sorted(set(to))[:4]))
        det.append(m.disp_detail(f['file']))
        rows.append([f['file'], '%s::%s' % (m.chain(owner), f['name']), 'void * member', f['line'],
                     S.fmt_tags(tags, det), f['qname']])
    nre = 0
    for p in S.unparsed_files():
        for d in S.member_decls(p):
            if d['static'] or d['fnptr'] or not d['ptr'] or d['ref']:
                continue
            if re.match(r'^(?:const\s+)?(?:volatile\s+)?void$', d.get('base', '')):
                tags = ['regex-only', 'buffer' if BUF_NAME.search(d['name']) and not OPAQUE_NAME.search(d['name'])
                        else 'opaque-handle']
                rows.append([p, '%s::%s' % (d['cls'], d['name']), 'void * member', d['line'],
                             S.fmt_tags(tags, ['type=%s' % d['type']]), d['cls'] + '::' + d['name']])
                nre += 1
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    out = [(r[0], r[5] if cnt[(r[0], r[1], r[2])] > 1 else r[1], r[2], r[3], r[4]) for r in rows]
    by = collections.Counter(r[2] for r in out)
    tagc = collections.Counter(t for r in out for t in S.tags_of(r[4]))
    note = ('virtual base specifiers %d + void * members %d (AST pointers and arrays of void *; %d regex-only); '
            'pretags %s') % (by['virtual base specifier'], by['void * member'], nre,
                             ' '.join('%s=%d' % kv for kv in sorted(tagc.items())))
    n = L.finish(CAT, out, note)
    for k, v in sorted(by.items()):
        print('  %-26s %d' % (k, v))
    for k, v in sorted(tagc.items()):
        print('  tag %-22s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
