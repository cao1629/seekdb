#!/usr/bin/env python3
"""c19-multi-owner: objects held by several containers, host back-pointers, timer-task hosts
(RULEBOOK §2.7 队列项 / 定时任务, §2.4 宿主或父对象的回指针, §2.5 ObDList, §5.2 #19).

Sites:
  A 'intrusive node also held elsewhere': a record definition that derives (transitively) from an intrusive
    link base and also
      - is an element/value type (by value or pointer) of a non-intrusive container member somewhere
        (template argument of a container field: ObSEArray, ObArray, ObHashMap, ObList, std::..., any
        replaced container/hash/queue template), or
      - is the element type of two or more distinct intrusive-list members (ObDList, SLL/DLL/Queue of
        ob_intrusive_list.h, ObLinkHashMap), or
      - derives from two or more distinct link bases.
    Link bases are derived from the database: records used as a base by another record that have a
    pointer member named next_/prev_/next/prev/next_link_/prev_link_/hazard_next_/link_ pointing to the
    record itself, a record of its own hierarchy, or a template parameter; plus LinkHashValue (the value
    base of ObLinkHashMap, whose hash_node_ points to the map's node).
  B 'host back-pointer': every c03 pointer member with pretag host-backptr (same rule, s3alib.Members.host_of:
    the pointee record or one of its bases holds the owner, or a non-infrastructure base of the owner, by
    value, by pointer, or as a container element; self-type pointers excluded).
  C 'timer task': every record definition deriving from a task base, i.e. a record that declares
    runTimerTask() pure virtual (ObTimerTask, ObTimeWheelTask).
  Files no TU parses are not scanned here (their pointer members are c03 regex-only rows; README).

symbol: A/C '<Class chain>'; B '<Class chain>::<field>' (same as the c03 row).

pretag:
  A  links=<base>+<base>, in=<Holder::member>(<Container>)... (at most 6), n_plain=<non-intrusive holders>,
     n_intrusive=<intrusive holders>
  B  module (host pointee is a module), owner-deletes (c03); host=<Pointee>(<member that holds the owner>)
  C  module-host (a host is a module), no-host (no record holds it as a member); host=<Class>(<member>[,ptr|ref])
     for each class holding a member of the task type (at most 6), backref=<task member>(<pointee>[,host])
     for the task's pointer/reference members
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c19-multi-owner'
LINK_NAMES = ('next_', 'prev_', 'next', 'prev', 'next_link_', 'prev_link_', 'hazard_next_', 'link_')
LINK_EXTRA = ('oceanbase::common::LinkHashValue',)
INTRUSIVE_FILES = ('src/oblib/lib/list/ob_dlist.h', 'src/oblib/lib/list/ob_intrusive_list.h',
                   'src/oblib/lib/hash/ob_link_hashmap.h')


def link_bases(m):
    used = collections.Counter()
    for u, r in m.defs.items():
        for b in r.get('bases', []):
            v = m.resolve_base(b)
            if v:
                used[v] += 1
    out = set()
    for u in sorted(used):
        r = m.defs.get(u)
        if r is None:
            continue
        fam = set([u] + list(m.ancestors(u)) + list(m.derived(u)))
        for f, es in m.member_edges(u):
            if f['name'] not in LINK_NAMES:
                continue
            for k, t, h in es:
                if k == 'ptr' and h == 'direct' and (t in fam or (isinstance(t, str) and t.startswith('param:'))):
                    out.add(u)
    for q in LINK_EXTRA:
        v = m.resolve(q)
        if v:
            out.add(v)
    return out


def container_holders(m):
    """{record usr: [(holder 'Class::field', container short name, intrusive bool, file, line)]}."""
    out = collections.defaultdict(list)
    for f in m.db.records:
        if f['kind'] != 'field' or f.get('generated'):
            continue
        ct = f.get('canonical_type', '') or ''
        if '<' not in ct:
            continue
        refs = m.type_refs(ct)
        owner = m.owner_of_field(f)
        holder = '%s::%s' % (m.chain(owner), f['name'])
        for r in refs:
            if r['depth'] < 1 or r['builtin'] or r.get('param') or r['tmpl'] is None:
                continue
            u = m.resolve(r['name'])
            if u is None:
                continue
            tu = m.resolve(r['tmpl']['name'])
            if tu is None or not m.is_transparent(tu, r['tmpl']['name']):
                continue
            trec = m.defs.get(tu)
            intrusive = bool(trec and trec['file'] in INTRUSIVE_FILES)
            out[u].append((holder, m.chain(tu), intrusive, f['file'], f['line']))
    return out


def main():
    m = S.Model(funcs=True)
    m.add_service_modules()
    mem = S.Members(m)
    rows = []
    # ---------------------------------------------------------------- A
    lb = link_bases(m)
    holders = container_holders(m)
    na = 0
    for u in sorted(m.defs):
        r = m.defs[u]
        if r.get('generated') or u in lb:
            continue
        anc = [a for a in m.ancestors(u) if a in lb]
        if not anc:
            continue
        hs = holders.get(u, [])
        plain = sorted(set(h[0] + '(' + h[1] + ')' for h in hs if not h[2]))
        intr = sorted(set(h[0] + '(' + h[1] + ')' for h in hs if h[2]))
        if not (plain or len(intr) >= 2 or len(set(anc)) >= 2):
            continue
        det = ['links=%s' % '+'.join(sorted(set(m.chain(a) for a in anc)))]
        lst = plain + intr
        if lst:
            det.append('in=%s' % ' '.join(lst[:6]))
        det.append('n_plain=%d' % len(plain))
        det.append('n_intrusive=%d' % len(intr))
        det.append(m.disp_detail(r['file']))
        rows.append([r['file'], m.chain(u), 'intrusive node also held elsewhere', r['line'],
                     S.fmt_tags([], det), r['qname']])
        na += 1
    # ---------------------------------------------------------------- B
    nb = 0
    fields = [f for f in m.db.records if f['kind'] == 'field' and not f.get('generated')]
    fields.sort(key=lambda r: (r['file'], r['line'], r.get('col', 0), r['usr']))
    for f in fields:
        ck = f.get('canonical_kind')
        if ck == 'pointer':
            pass
        elif ck in ('constant_array', 'array', 'incomplete_array') and f.get('element_kind') == 'pointer':
            pass
        else:
            continue
        es = [e for e in m.field_edges(f) if e[0] == 'ptr']
        tgt = es[0][1] if es else None
        owner = m.owner_of_field(f)
        host = mem.host_of(owner, tgt)
        if not host:
            continue
        tags = []
        if tgt in m.modules:
            tags.append('module')
        dels, rels = mem.cleanup(owner, f['name'])
        if dels:
            tags.append('owner-deletes')
        rows.append([f['file'], '%s::%s' % (m.chain(owner), f['name']), 'host back-pointer', f['line'],
                     S.fmt_tags(tags, ['host=%s' % host, 'pointee=%s' % m.chain(tgt), m.disp_detail(f['file'])]),
                     f['qname']])
        nb += 1
    # ---------------------------------------------------------------- C
    task_bases = set()
    for r in m.db.records:
        if r['kind'] == 'method' and r['name'] == 'runTimerTask' and r.get('is_pure') and \
                r.get('parent_usr') in m.defs:
            task_bases.add(r['parent_usr'])
    task_holders = collections.defaultdict(list)
    for f in fields:
        owner = m.owner_of_field(f)
        for k, t, h in m.field_edges(f):
            if k in ('value', 'ptr', 'ref', 'infra') and t in m.defs:
                task_holders[t].append((owner, f['name'], k))
    nc = 0
    for u in sorted(m.defs):
        r = m.defs[u]
        if r.get('generated') or u in task_bases:
            continue
        if not any(a in task_bases for a in m.ancestors(u)):
            continue
        hs = sorted(set((m.chain(o), fn, k) for o, fn, k in task_holders.get(u, [])))
        tags = []
        det = []
        host_usrs = sorted(set(o for o, fn, k in task_holders.get(u, [])))
        for c, fn, k in hs[:6]:
            det.append('host=%s(%s%s)' % (c, fn, '' if k in ('value', 'infra') else ',%s' % k))
        if any(h in m.modules for h in host_usrs):
            tags.append('module-host')
        if not hs:
            tags.append('no-host')
        back = []
        for f2 in m.fields(u):
            for k, t, h in m.field_edges(f2):
                if k in ('ptr', 'ref') and t in m.defs:
                    back.append('%s(%s%s)' % (f2['name'], m.chain(t), '' if t not in host_usrs else ',host'))
                    break
        if back:
            det.append('backref=%s' % ' '.join(back[:6]))
        det.append('bases=%s' % '+'.join(sorted(set(m.chain(a) for a in m.ancestors(u) if a in task_bases))))
        det.append(m.disp_detail(r['file']))
        rows.append([r['file'], m.chain(u), 'timer task', r['line'], S.fmt_tags(tags, det), r['qname']])
        nc += 1
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    out = [(r[0], r[5] if cnt[(r[0], r[1], r[2])] > 1 else r[1], r[2], r[3], r[4]) for r in rows]
    note = ('A intrusive-link nodes also held by another container or by two intrusive lists %d (link bases: %d, '
            'derived from the database) + B host back-pointers %d (c03 host-backptr) + C timer tasks %d '
            '(runTimerTask bases: %s)') % (na, len(lb), nb, nc, '+'.join(sorted(m.chain(t) for t in task_bases)))
    n = L.finish(CAT, out, note)
    print('  link bases:', ' '.join(sorted(m.chain(u) for u in lb)))
    print('  A=%d B=%d C=%d' % (na, nb, nc))
    return n


if __name__ == '__main__':
    main()
