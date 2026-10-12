#!/usr/bin/env python3
"""c07-locks: one row per (class, lock member) and one row per accessor returning a lock reference
(RULEBOOK §2.7 locks table and "数据放进锁的规则", 0.8, §5.2 #7).

Lock family (derived from the database, not typed in by hand): the record qualified names of RULEBOOK
§2.7's table and amendment 02-sync row 8 (`LOCK_FAMILY` below), every record deriving from one of them
(astdb `derived`, which follows template bases), template instantiations of the template members
(`ObSmallSpinLock<...>`), typedefs (fields are matched on their canonical type), and the pthread/std
primitives.  A member is a lock member when its canonical type, after removing const/volatile and array
extents, is a family type (`lock member`, `lock array member`), or a pointer/reference to one (`lock pointer
member`, `lock reference member`: a lock owned elsewhere); a class deriving from a family type gets a
`lock base class` row.

Body analysis (comment/string-stripped bodies of the methods of the owner class and of every class
deriving from it; regex level):
  lock region  from a guard declaration `<Type> g(L…)` / `<Type> g{L…}` (Type ending in Guard/Lock/
               Locker, typedefs included) to the end of its block; from an explicit `L.lock()`/`wrlock`/
               `rdlock`/`try_*lock*` call to the next `L.*unlock*()` of the same function or the end of the
               function (`cross-function-lock`); L is the member, `this->L`, `*L` or `L[...]`
  writes       `f = …`, `f op= …`, `++f`/`f++`, `f.push_back/set_refactored/erase_refactored/reset/reuse/
               clear/assign/insert/remove/add_last/push/pop…(` where f is a data member of the method's class
Pretag: `lock=<family type>-><RULEBOOK §2.7 target>`, `writes-under-lock=<fields>`, `reads-outside=<the
written fields also used outside every region of this lock, constructors/destructors excluded>`,
`returns-lock-ref=<accessor>`, `reentry-site=<n>` (is_wrlocked_by/is_locked_by_self/self_locked on L),
`unlock-calls=<n>`, `cross-function-lock`, `external-guards=<n>` (guards on `x.L`/`x->L` in functions of
other classes where x's declared type is the owner), `unit=<disposition>` for units not translated.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
CAT = 'c07-locks'

# RULEBOOK §2.7 (and amendment 02-sync row 8) -> Rust target
LOCK_FAMILY = collections.OrderedDict([
    ('oceanbase::common::ObSpinLock', 'Latch'),
    ('oceanbase::lib::ObMutex', 'Latch'),
    ('oceanbase::common::ObLatchMutex', 'Latch'),
    ('oceanbase::common::SpinRWLock', 'RwLatch'),
    ('oceanbase::common::ObLatch', 'RwLatch'),
    ('oceanbase::obsys::ObRWLock', 'RwLatch(amendment 02-sync)'),
    ('oceanbase::common::DRWLock', 'ShardedRwLatch'),
    ('oceanbase::common::TCRWLock', 'BigReaderLatch'),
    ('oceanbase::common::ObQSyncLock', 'QSyncLatch'),
    ('oceanbase::common::ObQSyncImpl', 'Quiescence'),
    ('oceanbase::common::ObQSyncWrapper', 'Quiescence'),
    ('oceanbase::common::ObQSync', 'Quiescence'),
    ('oceanbase::common::ObDynamicQSync', 'Quiescence'),
    ('oceanbase::common::ObBucketLock', 'BucketLatch'),
    ('oceanbase::common::ObBucketQSyncLock', 'BucketLatch(dead unit)'),
    ('oceanbase::common::ObRecursiveMutex', 'Latch+guard-passing(reentry rule)'),
    ('oceanbase::common::ObThreadCond', 'Monitor'),
    ('obutil::ObMonitor', 'Monitor'),
    ('obutil::ObUtilMutex', 'Latch'),
    ('obutil::Cond', 'Monitor'),
    ('oceanbase::common::ObCond', 'ObCond(amendment 02-sync)'),
    ('oceanbase::common::SimpleCond', 'FutexCond'),
    ('oceanbase::common::ObLightyCond', 'FutexCond'),
    ('oceanbase::lib::ObFutex', 'FutexCond'),
    ('oceanbase::common::LWaitCond', 'FutexCond'),
    ('oceanbase::common::SCondTemp', 'SCondTemp(amendment 02-sync)'),
    ('oceanbase::memtable::ObRowLatch', 'bitlock(TAS)'),
    ('oceanbase::common::ObSmallSpinLock', 'bitlock(backoff)'),
    ('oceanbase::common::ObPtrSpinLock', 'bitlock(backoff)'),
    ('oceanbase::transaction::CtxLock', 'CtxLock(rule 6: hooked guard)'),
    ('oceanbase::common::ObNullLock', 'none(amendment 02-sync: &mut self)'),
    ('oceanbase::common::hash::NLock', 'none'),
    ('oceanbase::common::hash::NCond', 'none'),
    ('oceanbase::common::DIRWLock', 'unlisted(§2.7 has no row)'),
    ('oceanbase::common::RingSpinLock', 'unlisted(§2.7 has no row)'),
    ('oceanbase::storage::MemtableMgrLock', 'unlisted(type-erased lock handle)'),
    ('oceanbase::lib::ISetLocker', 'unlisted(allocator set locker)'),
])
EXTERNAL_LOCKS = collections.OrderedDict([
    ('_opaque_pthread_mutex_t', ('pthread_mutex_t', 'Monitor/Latch')),
    ('_opaque_pthread_cond_t', ('pthread_cond_t', 'Monitor')),
    ('_opaque_pthread_rwlock_t', ('pthread_rwlock_t', 'RwLatch')),
    ('pthread_spinlock_t', ('pthread_spinlock_t', 'Latch')),
    ('std::mutex', ('std::mutex', 'Monitor/Latch')),
    ('std::recursive_mutex', ('std::recursive_mutex', 'Latch+guard-passing(reentry rule)')),
    ('std::timed_mutex', ('std::timed_mutex', 'Latch')),
    ('std::shared_mutex', ('std::shared_mutex', 'RwLatch')),
    ('std::condition_variable', ('std::condition_variable', 'Monitor')),
    ('std::condition_variable_any', ('std::condition_variable_any', 'Monitor')),
    ('easy_spinrwlock_t', ('easy_spinrwlock_t', 'libeasy remnant')),
    ('easy_spin_t', ('easy_spin_t', 'libeasy remnant')),
])
MUTATORS = ('push_back|push_front|pop_back|pop_front|set_refactored|erase_refactored|reset|reuse|clear|assign|insert|'
            'remove|add_last|add_first|add_before|add_after|push|pop|append|prepend|set|del|erase|emplace_back|'
            'reserve|resize|destroy|init|store|inc|dec|add|sub')
RX_GUARD_TYPE = re.compile(r'(?:Guard|Lock|Locker|LockT)\w*(?:\s*<[^;(){}]*>)?$')


def norm_type(t):
    t = re.sub(r'\b(?:const|volatile|struct|class|union)\b', '', t or '')
    return ' '.join(t.split())


class Family(object):
    def __init__(self, db):
        self.db = db
        self.by_qname = {}       # family record qname -> (seed qname, target)
        for q, tgt in LOCK_FAMILY.items():
            us = [r['usr'] for r in db.find(q, astdb.RECORD_KINDS) if r.get('is_definition')]
            if not us:
                continue
            self.by_qname[q] = (q, tgt)
            for u in us:
                for d in sorted(db.derived(u)):
                    dd = db.definition(d, astdb.RECORD_KINDS)
                    if dd is not None and dd['qname'] not in self.by_qname:
                        self.by_qname[dd['qname']] = (q, tgt)

    def classify(self, canonical):
        """(seed name, target, shape) for a canonical type, shape in member/array/pointer/reference; or None."""
        t = norm_type(canonical)
        shape = 'member'
        if re.search(r'\[[^\]]*\]$', t):
            shape = 'array'
            t = re.sub(r'(\s*\[[^\]]*\])+$', '', t).strip()
        if t.endswith('&'):
            shape = 'reference' if shape == 'member' else shape
            t = t[:-1].strip()
        if t.endswith('*'):
            if t.count('*') > 1:
                return None
            shape = 'pointer' if shape == 'member' else shape + '-of-pointers'
            t = t[:-1].strip()
        base = re.sub(r'<.*>$', '', t).strip()
        if base in self.by_qname:
            seed, tgt = self.by_qname[base]
            return (seed.split('::')[-1] if base == seed else '%s(<:%s)' % (base.split('::')[-1], seed.split('::')[-1]),
                    tgt, shape)
        if t in EXTERNAL_LOCKS or base in EXTERNAL_LOCKS:
            nm, tgt = EXTERNAL_LOCKS.get(t) or EXTERNAL_LOCKS[base]
            return (nm, tgt, shape)
        return None


def owner_class(db, field):
    """Nearest named (non-anonymous) record enclosing a field: methods belong to it."""
    pu = field.get('parent_usr')
    seen = 0
    while pu and seen < 10:
        d = db.definition(pu, astdb.RECORD_KINDS)
        if d is None:
            return None
        if not d.get('is_anonymous'):
            return d
        pu = d.get('parent_usr')
        seen += 1
    return None


def lock_regions(text, lname):
    """[(start, end, kind)] regions of the body text (relative offsets) where member lname is held."""
    regions = []
    blocks = S.brace_blocks(text)
    acc = r'(?:\*\s*)?(?:this\s*->\s*)?%s\b(?:\s*\[[^\]]*\])?' % re.escape(lname)
    rx_guard = re.compile(r'\b((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*<[^;(){}]*?>)?(?:\s*::\s*[A-Za-z_]\w*)*)\s+'
                          r'([A-Za-z_]\w*)\s*[({]\s*%s' % acc)
    for m in rx_guard.finditer(text):
        typ = m.group(1)
        if not RX_GUARD_TYPE.search(typ) or typ in ('return', 'else'):
            continue
        blk = S.innermost_block(blocks, m.start())
        end = blk[1] if blk else len(text)
        regions.append((m.start(), end, 'guard'))
    rx_lock = re.compile(r'%s\s*(?:\.|->)\s*((?:try_)?(?:wr|rd|r|w)?_?lock\w*|lock_for_\w+)\s*\(' % acc)
    rx_unlock = re.compile(r'%s\s*(?:\.|->)\s*((?:wr|rd|r|w)?_?unlock\w*)\s*\(' % acc)
    unlocks = [m.start() for m in rx_unlock.finditer(text)]
    cross = False
    for m in rx_lock.finditer(text):
        nxt = [u for u in unlocks if u > m.start()]
        if nxt:
            regions.append((m.start(), nxt[0], 'explicit'))
        else:
            regions.append((m.start(), len(text), 'explicit-to-end'))
            cross = True
    return regions, len(unlocks), cross


def writes_in(text, start, end, fields):
    out = set()
    seg = text[start:end]
    for m in re.finditer(r'(?<![\w.>])(?:this\s*->\s*)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)', seg):
        if m.group(1) in fields:
            out.add(m.group(1))
    for m in re.finditer(r'(?:\+\+|--)\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\b', seg):
        if m.group(1) in fields:
            out.add(m.group(1))
    for m in re.finditer(r'(?<![\w.>])(?:this\s*->\s*)?([A-Za-z_]\w*)\s*(?:\.|->)\s*(?:%s)\s*\(' % MUTATORS, seg):
        if m.group(1) in fields:
            out.add(m.group(1))
    return out


def uses_outside(text, regions, names):
    out = set()
    if not names:
        return out
    rx = re.compile(r'(?<![\w.>])(?:this\s*->\s*)?(%s)\b' % '|'.join(re.escape(n) for n in sorted(names)))
    for m in rx.finditer(text):
        p = m.start()
        if not any(a <= p < b for a, b, _k in regions):
            out.add(m.group(1))
    return out


def fmt_list(names, cap=40):
    names = sorted(names)
    if len(names) > cap:
        return ','.join(names[:cap]) + ',+%d' % (len(names) - cap)
    return ','.join(names)


def main():
    db = S.load_db(astdb.FUNC_KINDS + astdb.RECORD_KINDS + ('field',))
    env = S.TypeEnv(db)
    fam = Family(db)
    # ---- lock members
    members = collections.OrderedDict()     # (owner_usr, member name) -> info
    for f in db.by_kind('field'):
        if f.get('generated'):
            continue
        c = fam.classify(f.get('canonical_type') or f.get('type'))
        if c is None:
            continue
        owner = owner_class(db, f)
        if owner is None or owner.get('generated'):
            continue
        key = (owner['usr'], f['name'], f['usr'])
        members[key] = {'field': f, 'owner': owner, 'lock': c}
    bases_rows = []
    for r in db.by_kind(*astdb.RECORD_KINDS):
        if not r.get('is_definition') or r.get('generated'):
            continue
        for b in r.get('bases', []) or []:
            c = fam.classify(b.get('qname', ''))
            if c is not None:
                bases_rows.append((r, b, c))
    # ---- bodies by owner
    defs = S.definitions_with_bodies(db)
    by_parent = collections.defaultdict(list)
    for d, sp in defs:
        if d.get('parent_usr'):
            by_parent[d['parent_usr']].append((d, sp))
    derived_cache = {}

    def scope_classes(ou):
        if ou not in derived_cache:
            derived_cache[ou] = [ou] + sorted(db.derived(ou))
        return derived_cache[ou]

    lock_names = {k[1] for k in members}
    # ---- external guard sites: guards on x.L / x->L in other classes' functions
    ext = collections.Counter()
    rx_ext = re.compile(r'\b((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*<[^;(){}]*?>)?)\s+[A-Za-z_]\w*\s*[({]\s*'
                        r'(?:\*\s*)?([A-Za-z_]\w*)\s*(?:\.|->)\s*([A-Za-z_]\w*)\b')
    for d, (ob, cb) in defs:
        text = L.stripped(d['file'])[ob:cb + 1]
        if 'Guard' not in text and 'Lock' not in text:
            continue
        for m in rx_ext.finditer(text):
            if m.group(3) not in lock_names or not RX_GUARD_TYPE.search(m.group(1)):
                continue
            t, _o = env.var_type(d, text, m.group(2))
            ru = env.record_of_type(t)
            if ru is None:
                continue
            for au in [ru] + db.ancestors(ru):
                hits = [k for k in members if k[0] == au and k[1] == m.group(3)]
                if hits:
                    if d.get('parent_usr') not in scope_classes(au):
                        ext[hits[0]] += 1
                    break
    # ---- accessors returning lock references
    accessors = []
    seen_usr = set()
    for r in db.by_kind(*astdb.FUNC_KINDS):
        if r['usr'] in seen_usr:
            continue
        seen_usr.add(r['usr'])
        c = db.canonical(r['usr'])
        if c is None or c.get('generated'):
            continue
        rt = c.get('canonical_return_type') or ''
        if not re.search(r'[*&]\s*$', rt):
            continue
        lk = fam.classify(rt)
        if lk is None or lk[2] not in ('pointer', 'reference'):
            continue
        accessors.append((c, lk))
    acc_returns = {}         # usr -> member name returned
    for c, lk in accessors:
        for d in db.get(c['usr']):
            if not d.get('is_definition') or d.get('generated'):
                continue
            sp = S.body_span(d)
            if sp is None:
                continue
            text = L.stripped(d['file'])[sp[0]:sp[1] + 1]
            m = re.search(r'\breturn\s+(?:&\s*)?\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)(?:\s*\[[^\]]*\])?\s*\)?\s*;', text)
            if m:
                acc_returns[c['usr']] = m.group(1)
                break
    # ---- per lock member analysis
    rows = []
    for (ou, name, fusr), info in members.items():
        f, owner, (lname, target, shape) = info['field'], info['owner'], info['lock']
        writes, outside_uses, written_all = set(), set(), set()
        n_unlock = n_reentry = 0
        cross = False
        regions_by_body = []
        for cu in scope_classes(ou):
            fields = set(env.fields(cu).keys()) - {name}
            for d, (ob, cb) in by_parent.get(cu, ()):
                text = L.stripped(d['file'])[ob:cb + 1]
                if name not in text:
                    regions_by_body.append((d, text, [], fields))
                    continue
                regions, nu, cr = lock_regions(text, name)
                n_unlock += nu
                cross = cross or cr
                n_reentry += len(re.findall(r'(?:this\s*->\s*)?%s\b\s*(?:\.|->)\s*(?:is_wrlocked_by\w*|is_locked_by_self|'
                                            r'self_locked|is_locked_by\w*)\s*\(' % re.escape(name), text))
                for a, b, _k in regions:
                    w = writes_in(text, a, b, fields)
                    writes |= {'%s' % x for x in w}
                regions_by_body.append((d, text, regions, fields))
        written_all = set(writes)
        for d, text, regions, fields in regions_by_body:
            if d['kind'] in ('ctor', 'dtor'):
                continue
            outside_uses |= uses_outside(text, regions, written_all & fields)
        pre = ['lock=%s->%s' % (lname, target)]
        if writes:
            pre.append('writes-under-lock=%s' % fmt_list(writes))
        if outside_uses:
            pre.append('reads-outside=%s' % fmt_list(outside_uses))
        rets = sorted(S.short_qname(c['qname']) for c, _lk in accessors
                      if acc_returns.get(c['usr']) == name and c.get('parent_usr') in scope_classes(ou))
        if rets:
            pre.append('returns-lock-ref=%s' % ','.join(rets[:6]))
        if n_reentry:
            pre.append('reentry-site=%d' % n_reentry)
        if n_unlock:
            pre.append('unlock-calls=%d' % n_unlock)
        if cross:
            pre.append('cross-function-lock')
        if ext.get((ou, name, fusr)):
            pre.append('external-guards=%d' % ext[(ou, name, fusr)])
        if not regions_by_body:
            pre.append('no-method-bodies')
        un = S.unit_of(f['file'])
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        cons = {'member': 'lock member', 'array': 'lock array member', 'pointer': 'lock pointer member',
                'reference': 'lock reference member'}.get(shape, 'lock %s member' % shape)
        sym = '%s::%s' % (S.short_qname(owner['qname']), name)
        rows.append([f['file'], sym, cons, f['line'], '; '.join(pre)])
    for r, b, (lname, target, shape) in bases_rows:
        un = S.unit_of(r['file'])
        pre = ['lock=%s->%s' % (lname, target), 'base-access=%s' % (b.get('access') or '')]
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([r['file'], '%s::<base %s>' % (S.short_qname(r['qname']), S.short_qname(b.get('qname', ''))),
                     'lock base class', r['line'], '; '.join(pre)])
    for c, (lname, target, shape) in accessors:
        un = S.unit_of(c['file'])
        pre = ['lock=%s->%s' % (lname, target), 'returns=%s' % shape]
        if acc_returns.get(c['usr']):
            pre.append('returns-member=%s' % acc_returns[c['usr']])
        if c.get('is_const'):
            pre.append('const-method')
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        ps = ', '.join((p.get('canonical_type') or p.get('type') or '?') for p in c.get('params', []) or [])
        rows.append([c['file'], '%s(%s)%s' % (S.short_qname(c['qname']), ps.replace('oceanbase::', ''),
                                              ' const' if c.get('is_const') else ''),
                     'lock accessor', c['line'], '; '.join(pre)])
    rows += fallback_rows(fam)
    seen = collections.Counter()
    for r in sorted(rows, key=lambda r: (r[0], r[3], r[1], r[2])):
        k = (r[0], r[1], r[2])
        seen[k] += 1
        if seen[k] > 1:
            r[1] = '%s#%d' % (r[1], seen[k])
    nmem = sum(1 for r in rows if r[2].startswith('lock') and r[2].endswith('member'))
    nacc = sum(1 for r in rows if r[2] == 'lock accessor')
    nbase = sum(1 for r in rows if r[2] == 'lock base class')
    nfb = sum(1 for r in rows if 'regex' in r[2])
    classes = len({r[1].rsplit('::', 1)[0] for r in rows if r[2].endswith('member')})
    L.finish(CAT, rows, 'lock-family members (%d in %d classes; family = RULEBOOK §2.7 types + derived records + '
                        'pthread/std) + %d lock base classes + %d accessors returning a lock pointer/reference + %d regex '
                        'rows for files no TU parses; pretag = regex-level writes under the lock, reads outside, '
                        'reentry and unlock counts' % (nmem - nfb, classes, nbase, nacc, nfb))


RX_FB_MEMBER = re.compile(r'^[ \t]*(?:mutable\s+)?((?:[A-Za-z_]\w*\s*::\s*)*(?:%s)(?:\s*<[^;(){}]*>)?)\s*([*&]?)\s*'
                          r'([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*(?:CACHE_ALIGNED\s*)?;' % '|'.join(
                              sorted({q.split('::')[-1] for q in LOCK_FAMILY} | {'pthread_mutex_t', 'pthread_cond_t',
                                                                               'pthread_rwlock_t', 'pthread_spinlock_t'},
                                     key=len, reverse=True)), re.M)


def fallback_rows(fam):
    rows = []
    for p in S.tus0_files():
        text = L.stripped(p)
        for m in RX_FB_MEMBER.finditer(text):
            cls = L.enclosing_class(p, m.start(3))
            if not cls:
                continue
            shape = {'*': 'pointer', '&': 'reference'}.get(m.group(2), 'member')
            if m.group(4):
                shape = 'array'
            cons = {'member': 'lock member', 'array': 'lock array member', 'pointer': 'lock pointer member',
                    'reference': 'lock reference member'}[shape] + ' (regex; file not parsed)'
            rows.append([p, '%s::%s' % (cls, m.group(3)), cons, L.line_of(p, m.start(3)),
                         'lock=%s; regex-fallback(tus=0)' % ' '.join(m.group(1).split())])
    return rows


if __name__ == '__main__':
    main()
