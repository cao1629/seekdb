#!/usr/bin/env python3
"""c05-self-arena: classes that own an allocator and allocate their own members from it; members that
default to the class's own allocator (RULEBOOK 0.7 自持 arena 的对象 (a)(b)(c), §5.2 #5).

Arena types (derived from the database, s3alib.Model.allocators): ObIAllocator and every record derived
from it (ObArenaAllocator, ObSafeArenaAllocator, ObFIFOAllocator, ObConcurrentFIFOAllocator,
ObSmallBlockAllocator, ObWrapperAllocator, ...), plus records named *Alloc*/*Arena* that define alloc()
(PageArena, ModulePageAllocator, ObFifoArena, ObSliceAlloc, ...), plus lib::MemoryContext (a context handle
the class creates and destroys).

Sites:
  'self-arena class'                  one row per class (record definition, first-party) that
     (1) owns an allocator: a data member of an arena type held by value (or an array of them), or a
         pointer to an arena type that the class itself frees (c03 owner-deletes) or placement-news into
         its own buffer, and
     (2) has members that can be allocated from it: object pointers (not to modules, not function
         pointers, not the allocator pointers themselves), ObString members, container members
         (template instances from replace:container/hash/... units or std::), or a constructor init list
         that passes the allocator member to another member
  'member defaults to own allocator'  one row per pointer/reference member that a constructor (init list or
     body) or a default member initializer points at one of the class's own by-value allocators
     (allocator_(&arena_), ObIAllocator *alloc_ = &inner_, alloc_(arena_) for references)
  files no TU parses: regex fallback (class rows only when both member kinds are visible), pretag regex-only

symbol: '<Class chain>' for class rows, '<Class chain>::<member>' for member rows.

pretag: scope tags crosses-calls (class or a base named like a cursor/result set/iterator/operator/row store),
long-lived (module or reachable from a module/session/schema by value, has a lock member, or lives in
share/schema, sql/session, sql/plan_cache, storage meta/tablet/ls/tx, share/cache), request-scope
(compile-time IR or a query directory and not long-lived), unclassified (none of these); details:
arena=<members>, users=<n>(ptr=a,str=b,array=c), passes-arena=<members>, init=<how>:<expr>, also-external
(member rows: some constructor or method also sets it from elsewhere), disp=<unit disposition>.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c05-self-arena'
MEMCTX = ('oceanbase::lib::MemoryContext',)


def scope_tags(m, owner):
    hints = m.scope_hints(owner)
    tags = []
    if 'crosses-calls' in hints:
        tags.append('crosses-calls')
    if 'long-lived-candidate' in hints:
        tags.append('long-lived')
    elif 'query-candidate' in hints or 'in-ir-node' in hints:
        tags.append('request-scope')
    if not tags:
        tags.append('unclassified')
    return tags


def main():
    m = S.Model(funcs=True)
    m.add_service_modules()
    mem = S.Members(m)
    memctx = set(u for q in MEMCTX for u in [m.resolve(q)] if u)
    arena_types = set(m.allocators) | memctx
    rows = []
    owners = sorted(set(m.owner_of_field(f) for f in m.db.records
                        if f['kind'] == 'field' and not f.get('generated')))
    for owner in owners:
        rec = m.defs.get(owner)
        if rec is None or rec.get('generated') or rec.get('is_anonymous'):
            continue
        fields = m.fields(owner)
        arenas, users = [], collections.Counter()
        alloc_ptr_names = set()
        names = []
        for f in fields:
            es = m.field_edges(f)
            ck = f.get('canonical_kind')
            if any(k in ('value', 'infra') and t in arena_types and h in ('direct', 'array') for k, t, h in es):
                arenas.append(f['name'])
                continue
            ptrs = [t for k, t, h in es if k == 'ptr']
            if ck == 'pointer' and ptrs and ptrs[0] in arena_types:
                alloc_ptr_names.add(f['name'])
                dels, rels = mem.cleanup(owner, f['name'])
                ss = mem.self_sibling(owner, f['name'])
                if dels or (ss and ss[0] == 'placement-new'):
                    arenas.append('*' + f['name'])
                continue
            if ck in ('lvalue_ref', 'rvalue_ref'):
                tg = [t for k, t, h in es if k == 'ref']
                if tg and tg[0] in arena_types:
                    alloc_ptr_names.add(f['name'])
                continue
            if any(k == 'str' for k, t, h in es):
                users['str'] += 1
                names.append(f['name'])
            elif any(k == 'ptr' and t not in m.modules and t != 'fn' and h == 'direct' or
                     (k == 'ptr' and h == 'array') for k, t, h in es):
                users['ptr'] += 1
                names.append(f['name'])
            elif ck == 'record':
                top = [r for r in m.type_refs(f.get('canonical_type', '')) if r['depth'] == 0 and not r['builtin']]
                if top:
                    u0 = m.resolve(top[0]['name'])
                    if m.is_transparent(u0, top[0]['name']) and u0 not in arena_types and \
                            '<' in (f.get('canonical_type') or ''):
                        users['array'] += 1
                        names.append(f['name'])
        if not arenas:
            continue
        own_vals = [a for a in arenas if not a.startswith('*')]
        passes = []
        inits = m.member_inits(owner)
        for fname, lst in sorted(inits.items()):
            if fname in own_vals or fname.lstrip('*') in arenas:
                continue
            for how, expr in lst:
                if how != 'init-list':
                    continue
                for a in own_vals:
                    if re.search(r'(?<![\w.>])&?\s*%s\b' % re.escape(a), expr) and fname not in alloc_ptr_names:
                        passes.append(fname)
        passes = sorted(set(passes))
        n_users = sum(users.values())
        if n_users == 0 and not passes:
            continue
        tags = scope_tags(m, owner)
        det = ['arena=%s' % '+'.join(arenas),
               'users=%d(ptr=%d,str=%d,array=%d)' % (n_users, users['ptr'], users['str'], users['array'])]
        if passes:
            det.append('passes-arena=%s' % '+'.join(passes[:8]))
        det.append(m.disp_detail(rec['file']))
        rows.append([rec['file'], m.chain(owner), 'self-arena class', rec['line'], S.fmt_tags(tags, det),
                     rec['qname']])
    # member rows: pointer/reference members defaulting to an own by-value allocator
    for owner in owners:
        rec = m.defs.get(owner)
        if rec is None or rec.get('generated') or rec.get('is_anonymous'):
            continue
        fields = m.fields(owner, inherited=True)
        own_vals = set()
        for f in fields:
            es = m.field_edges(f)
            if any(k in ('value', 'infra') and t in arena_types and h in ('direct', 'array') for k, t, h in es):
                own_vals.add(f['name'])
        if not own_vals:
            continue
        inits = m.member_inits(owner)
        assigns = m.member_assignments(owner)
        for f in m.fields(owner):
            ck = f.get('canonical_kind')
            if ck not in ('pointer', 'lvalue_ref', 'rvalue_ref'):
                continue
            hit = None
            others = 0
            for how, expr in inits.get(f['name'], []):
                e = expr.strip()
                a = S.addr_member(e) if ck == 'pointer' else (re.match(r'^\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\)?$', e)
                                                              .group(1) if re.match(r'^\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\)?$', e) else None)
                if a in own_vals and (ck != 'pointer' or '(' not in e):
                    if hit is None:
                        hit = (how, e)
                elif not re.match(r'^(?:NULL|nullptr|0)$', e):
                    others += 1
            for meth, rhs in assigns.get(f['name'], []):
                if not re.match(r'^(?:NULL|nullptr|0)$', rhs.strip()) and S.addr_member(rhs) not in own_vals:
                    others += 1
            if hit is None:
                continue
            tags = scope_tags(m, owner)
            det = ['init=%s:%s' % hit]
            if others:
                det.append('also-external=%d' % others)
            det.append(m.disp_detail(f['file']))
            rows.append([f['file'], '%s::%s' % (m.chain(owner), f['name']), 'member defaults to own allocator',
                         f['line'], S.fmt_tags(tags, det), f['qname']])
    # regex fallback for files no TU parses
    nre = 0
    for p in S.unparsed_files():
        by_cls = collections.defaultdict(list)
        for d in S.member_decls(p):
            if not d['static']:
                by_cls[d['cls']].append(d)
        for cls, ds in sorted(by_cls.items()):
            arena = [d['name'] for d in ds if not d['ptr'] and not d['ref'] and
                     re.search(r'(Arena|Allocator|MemoryContext)\b', d.get('base', ''))]
            usr = [d['name'] for d in ds if (d['ptr'] and not d['fnptr'] and
                                             not re.search(r'(Allocator|Arena)', d.get('base', ''))) or
                   re.match(r'^(?:common::)?ObString$', d.get('base', '')) or
                   re.search(r'Ob\w*Array<|ObHashMap<|ObList<', d.get('base', ''))]
            if arena and usr:
                line = min(d['line'] for d in ds)
                rows.append([p, cls, 'self-arena class', line,
                             S.fmt_tags(['regex-only'], ['arena=%s' % '+'.join(arena), 'users=%d' % len(usr)]), cls])
                nre += 1
    cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
    out = [(r[0], r[5] if cnt[(r[0], r[1], r[2])] > 1 else r[1], r[2], r[3], r[4]) for r in rows]
    by = collections.Counter(r[2] for r in out)
    tagc = collections.Counter()
    for r in out:
        if r[2] == 'self-arena class':
            for t in S.tags_of(r[4]):
                tagc[t] += 1
    note = ('self-arena classes %d (own an allocator by value, or a freed/placement-new allocator pointer, and have '
            'pointer/ObString/container members or pass the allocator to a member) + members defaulting to the own '
            'allocator %d; %d regex-only rows; class tags %s') % (
        by['self-arena class'], by['member defaults to own allocator'], nre,
        ' '.join('%s=%d' % kv for kv in sorted(tagc.items())))
    n = L.finish(CAT, out, note)
    for k, v in sorted(by.items()):
        print('  %-34s %d' % (k, v))
    for k, v in sorted(tagc.items()):
        print('  tag %-30s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
