#!/usr/bin/env python3
"""c03-ptr-members: every raw pointer data member (RULEBOOK §2.4 裸指针成员, §5.2 #3).

Sites (AST, first-party records only, generated files excluded):
  - fields whose canonical type is an object pointer (canonical_kind 'pointer', 6,400)  -> 'raw pointer member'
  - fields that are arrays of object pointers (T *x[N], T *x[]; 71)                     -> 'raw pointer array member'
  - fields whose canonical type is a function pointer, or an array of them              -> 'function pointer member'
  - files no TU parses (files.tsv tus = 0): regex fallback over class bodies            -> same constructs,
    pretag 'regex-only'
Pointer-to-member fields (35) are not raw pointers and are not listed (README blind spots).

symbol: '<Class chain>::<field>' (namespaces dropped, nested classes kept, anonymous struct/union members
flattened into their enclosing class); when two rows of one file get the same symbol, those rows use the
field's fully qualified name instead.

pretag (comma-separated hints, then ';'-separated details; see README.md for every rule):
  ir-node, plan-node, module, allocator, function-pointer, self-or-sibling, host-backptr, owner-deletes,
  owner-releases, const-pointee, self-type, void-pointee, byte-pointee, template-param, regex-only;
  details: pointee=<type>, init=<how>:<expr>, host=<Pointee>(<member>), frees=<method>,
  disp=<unit disposition when it is not translate>
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c03-ptr-members'
BYTE = ('char', 'unsigned char', 'signed char', 'uint8_t', 'int8_t')


def pointee_label(m, tgt):
    if tgt is None:
        return ''
    if tgt in m.defs:
        return m.chain(tgt)
    return tgt.split(':', 1)[-1]


def main():
    m = S.Model(funcs=True)
    m.add_service_modules()
    c = S.Members(m)
    rows = []
    fields = [r for r in m.db.records if r['kind'] == 'field' and not r.get('generated')]
    fields.sort(key=lambda r: (r['file'], r['line'], r.get('col', 0), r['usr']))
    for f in fields:
        ck = f.get('canonical_kind')
        ek = f.get('element_kind')
        if ck == 'pointer':
            cons = 'raw pointer member'
        elif ck in ('constant_array', 'array', 'incomplete_array') and ek == 'pointer':
            cons = 'raw pointer array member'
        elif ck == 'function_pointer' or (ck in ('constant_array', 'array', 'incomplete_array')
                                          and ek == 'function_pointer'):
            cons = 'function pointer member'
        else:
            continue
        owner = m.owner_of_field(f)
        sym = '%s::%s' % (m.chain(owner), f['name'])
        tags, det = [], []
        if cons == 'function pointer member':
            tags.append('function-pointer')
            det.append('type=%s' % m.strip_ns(f.get('type', '')))
        else:
            es = [e for e in m.field_edges(f) if e[0] == 'ptr']
            tgt = es[0][1] if es else None
            if tgt in m.ir:
                tags.append('ir-node')
            if tgt in m.plan_nodes:
                tags.append('plan-node')
            if tgt in m.modules:
                tags.append('module')
            if tgt in m.allocators:
                tags.append('allocator')
            ss = c.self_sibling(owner, f['name'])
            if ss:
                tags.append('self-or-sibling')
                det.append('init=%s:%s' % ss)
            host = c.host_of(owner, tgt)
            if host:
                tags.append('host-backptr')
                det.append('host=%s' % host)
            dels, rels = c.cleanup(owner, f['name'])
            if dels:
                tags.append('owner-deletes')
                det.append('frees=%s' % '+'.join(dels))
            if rels:
                tags.append('owner-releases')
                det.append('releases=%s' % '+'.join(rels))
            et = f.get('element_type', '') if cons == 'raw pointer array member' else ''
            if f.get('pointee_const') or et.startswith('const '):
                tags.append('const-pointee')
            if tgt in m.defs and (tgt == owner or tgt in m.ancestors(owner) or tgt in m.derived(owner)):
                tags.append('self-type')
            if tgt == 'builtin:void':
                tags.append('void-pointee')
            elif isinstance(tgt, str) and tgt.startswith('builtin:') and tgt.split(':', 1)[1] in BYTE:
                tags.append('byte-pointee')
            elif isinstance(tgt, str) and tgt.startswith('param:'):
                tags.append('template-param')
            det.insert(0, 'pointee=%s' % pointee_label(m, tgt))
        det.append(m.disp_detail(f['file']))
        rows.append([f['file'], sym, cons, f['line'], S.fmt_tags(tags, det), f['qname']])
    # regex fallback: files no TU parses
    nre = 0
    for p in S.unparsed_files():
        for d in S.member_decls(p):
            if d['static'] or not d['ptr'] or d['ref']:
                continue
            if d['fnptr']:
                cons, tags = 'function pointer member', ['function-pointer', 'regex-only']
            elif re.search(r'\[[^\]]*\]', d['type']):
                cons, tags = 'raw pointer array member', ['regex-only']
            else:
                cons, tags = 'raw pointer member', ['regex-only']
            if re.match(r'^(?:const\s+)?(?:void)\b', d.get('base', '')):
                tags.append('void-pointee')
            if d.get('base', '').startswith('const '):
                tags.append('const-pointee')
            rows.append([p, '%s::%s' % (d['cls'], d['name']), cons, d['line'],
                         S.fmt_tags(tags, ['type=%s' % d['type']]), d['cls'] + '::' + d['name']])
            nre += 1
    # unique symbols: fall back to the qualified name for colliding (file, symbol, construct) keys
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    out = []
    for r in rows:
        sym = r[1]
        if cnt[(r[0], r[1], r[2])] > 1:
            sym = r[5]
        out.append((r[0], sym, r[2], r[3], r[4]))
    by = collections.Counter(r[2] for r in out)
    tagc = collections.Counter()
    for r in out:
        for t in S.tags_of(r[4]):
            tagc[t] += 1
    reg = collections.Counter(r[2] for r in out if 'regex-only' in S.tags_of(r[4]))
    note = ('raw pointer members %d + raw pointer array members %d (AST %d + %d = the 6,471 of ast_validate) + function '
            'pointer members %d (AST %d); %d regex-only rows from files no TU parses are included; pretags %s') % (
        by['raw pointer member'], by['raw pointer array member'],
        by['raw pointer member'] - reg['raw pointer member'], by['raw pointer array member'] - reg['raw pointer array member'],
        by['function pointer member'], by['function pointer member'] - reg['function pointer member'], nre,
        ' '.join('%s=%d' % kv for kv in sorted(tagc.items())))
    n = L.finish(CAT, out, note)
    for k, v in sorted(by.items()):
        print('  %-30s %d' % (k, v))
    for k, v in sorted(tagc.items()):
        print('  tag %-26s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
