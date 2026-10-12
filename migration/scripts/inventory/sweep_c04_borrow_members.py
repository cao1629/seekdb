#!/usr/bin/env python3
"""c04-borrow-members: reference members, borrowed-allocator members, ObString members
(RULEBOOK §2.4 引用成员 / 借用分配器的成员 / ObString 成员, §5.2 #4; conclusions go to types.tsv).

Sites (AST, first-party records, generated files excluded; regex fallback for files no TU parses):
  'allocator member'  pointer or reference member whose pointee is an allocator type (ObIAllocator and
                      every record derived from it, plus records named *Alloc*/*Arena* that define alloc();
                      list in s3alib.Model.allocators)
  'reference member'  every other lvalue/rvalue reference member (1,269 references by AST minus the
                      allocator references above)
  'ObString member'   canonical type (const) oceanbase::common::ObString, or an array of it (1,055)
Allocator *pointers* are also c03 rows (pretag allocator); this category decides them (README).

symbol: '<Class chain>::<field>' as in c03.

pretag hints:
  containing class (all constructs; s3alib.Model.scope_hints): in-ir-node, in-plan, in-session, in-schema,
    long-lived-candidate, query-candidate, crosses-calls
  pointee (reference / allocator members): module, ir-node, plan-node, host-backref (pointee holds the
    owner), self-or-sibling (allocator member pointing at the owner's own arena: also c05), ctor-param
    (set from a constructor parameter), const-pointee
  details: pointee=<type>, init=<how>:<expr>, host=<P>(<member>), disp=<unit disposition if not translate>
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c04-borrow-members'


def is_obstring(f):
    ct = (f.get('canonical_type') or '').replace('const ', '').strip()
    if ct == S.OBSTRING:
        return True
    ck = f.get('canonical_kind')
    et = (f.get('element_type') or '').replace('const ', '').strip()
    return ck in ('constant_array', 'array', 'incomplete_array') and et == S.OBSTRING


def ctor_param_init(m, owner, fname):
    """True when a constructor initialises the member from one of its parameters."""
    for d in m.func_records(owner, ('ctor',)):
        pnames = set(p.get('name') for p in d.get('params', []) if p.get('name'))
        if not pnames:
            continue
        for how, expr in m.member_inits(owner).get(fname, []):
            e = re.sub(r'^[&*]\s*', '', expr.strip())
            if how in ('init-list', 'ctor-body') and e in pnames:
                return True
    return False


def main():
    m = S.Model(funcs=True)
    m.add_service_modules()
    mem = S.Members(m)
    rows = []
    fields = [r for r in m.db.records if r['kind'] == 'field' and not r.get('generated')]
    fields.sort(key=lambda r: (r['file'], r['line'], r.get('col', 0), r['usr']))
    for f in fields:
        ck = f.get('canonical_kind')
        cons = None
        tgt = None
        if ck in ('pointer', 'lvalue_ref', 'rvalue_ref'):
            es = [e for e in m.field_edges(f) if e[0] in ('ptr', 'ref')]
            tgt = es[0][1] if es else None
            if tgt in m.allocators:
                cons = 'allocator member'
            elif ck != 'pointer':
                cons = 'reference member'
        elif is_obstring(f):
            cons = 'ObString member'
        if cons is None:
            continue
        owner = m.owner_of_field(f)
        sym = '%s::%s' % (m.chain(owner), f['name'])
        tags = list(m.scope_hints(owner))
        det = []
        if cons != 'ObString member':
            if tgt in m.modules:
                tags.append('module')
            if tgt in m.ir:
                tags.append('ir-node')
            if tgt in m.plan_nodes:
                tags.append('plan-node')
            host = mem.host_of(owner, tgt)
            if host:
                tags.append('host-backref')
                det.append('host=%s' % host)
            ss = mem.self_sibling(owner, f['name'], is_ref=(ck != 'pointer'))
            if ss:
                tags.append('self-or-sibling')
                det.append('init=%s:%s' % ss)
            if ctor_param_init(m, owner, f['name']):
                tags.append('ctor-param')
            if f.get('pointee_const'):
                tags.append('const-pointee')
            lab = m.chain(tgt) if tgt in m.defs else (tgt.split(':', 1)[-1] if isinstance(tgt, str) else '')
            det.insert(0, 'pointee=%s%s' % (lab, '&' if ck != 'pointer' else '*'))
        else:
            if ck != 'record':
                det.append('array=%s' % m.strip_ns(f.get('type', '')))
        det.append(m.disp_detail(f['file']))
        rows.append([f['file'], sym, cons, f['line'], S.fmt_tags(tags, det), f['qname']])
    # regex fallback for files no TU parses
    nre = 0
    for p in S.unparsed_files():
        for d in S.member_decls(p):
            if d['static'] or d['fnptr']:
                continue
            base = re.sub(r'^(?:const\s+)', '', d.get('base', ''))
            cons = None
            if (d['ptr'] or d['ref']) and re.search(r'\b(ObIAllocator|\w*Allocator|\w*Arena\w*)\b', base):
                cons = 'allocator member'
            elif d['ref']:
                cons = 'reference member'
            elif not d['ptr'] and re.match(r'^(?:common::)?ObString$', base):
                cons = 'ObString member'
            if cons is None:
                continue
            rows.append([p, '%s::%s' % (d['cls'], d['name']), cons, d['line'],
                         S.fmt_tags(['regex-only'], ['type=%s' % d['type']]), d['cls'] + '::' + d['name']])
            nre += 1
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    out = [(r[0], r[5] if cnt[(r[0], r[1], r[2])] > 1 else r[1], r[2], r[3], r[4]) for r in rows]
    by = collections.Counter(r[2] for r in out)
    tagc = collections.defaultdict(collections.Counter)
    for r in out:
        for t in S.tags_of(r[4]):
            tagc[r[2]][t] += 1
    reg = collections.Counter(r[2] for r in out if 'regex-only' in S.tags_of(r[4]))
    note = ('allocator members %d (pointers/references to allocator types; AST %d) + reference members %d (other '
            'references; AST %d) + ObString members %d (AST %d); %d regex-only rows from files no TU parses are '
            'included; ObString pretags %s') % (
        by['allocator member'], by['allocator member'] - reg['allocator member'],
        by['reference member'], by['reference member'] - reg['reference member'],
        by['ObString member'], by['ObString member'] - reg['ObString member'], nre,
        ' '.join('%s=%d' % kv for kv in sorted(tagc['ObString member'].items())))
    n = L.finish(CAT, out, note)
    for k, v in sorted(by.items()):
        print('  %-30s %d' % (k, v))
    for c in sorted(tagc):
        print('  [%s] %s' % (c, ' '.join('%s=%d' % kv for kv in sorted(tagc[c].items()))))
    return n


if __name__ == '__main__':
    main()
