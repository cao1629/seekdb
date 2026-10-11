# Count lock declarations (members/variables), RAII guard instantiations, and manual
# lock/unlock calls in the stripped mirror (translation scope).
# Usage: python3 -I lock_sites.py <stripped_root>
import os, re, sys, collections

root = sys.argv[1]
LOCK_TYPES = ['ObLatch', 'ObLatchMutex', 'SpinRWLock', 'ObSpinLock', 'ObMutex', 'ObRecursiveMutex',
              'ObThreadCond', 'TCRWLock', 'DRWLock', 'ObBucketLock', 'ObQSyncLock', 'ObBucketQSyncLock',
              'ObRWLock', 'ObByteLock', 'ObSmallSpinLock', 'ObPtrSpinLock', 'SimpleCond', 'ObCond',
              'pthread_mutex_t', 'pthread_rwlock_t', 'pthread_cond_t', 'pthread_spinlock_t',
              'std::mutex', 'std::condition_variable', 'ObUtilMutex', 'ObMonitor', 'ObFutex', 'RWLock']
# declaration: [mutable] [ns::]Type [<...>] name [array] (; | ( | { | = | ,)
decl_re = {}
for t in LOCK_TYPES:
    esc = re.escape(t)
    decl_re[t] = re.compile(r'(?<![\w:])(?:(?:oceanbase::)?(?:common|lib|obutil)::)?' + esc +
                            r'(?:<[^;(){}]*?>)?\s+(?!operator\b)([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:;|=|\{|\(|,|CACHE_ALIGNED|__attribute__)')
guard_re = re.compile(r'(?<![\w])((?:[\w]+::)*\w*(?:Lock|Latch|Mutex|Cond|QSync|Critical)\w*Guard\w*)(?:<[^;(){}]*?>)?\s+(?!operator\b)[A-Za-z_]\w*\s*[\(\{]')
macro_guard_re = re.compile(r'(?<![\w])(CriticalGuard|CriticalGuardDynamic|SpinRLockGuard|SpinWLockGuard)\s*\(')
manual_re = re.compile(r'(?:\.|->)\s*(lock|unlock|rdlock|wrlock|rdunlock|wrunlock|try_rdlock|try_wrlock|trylock|try_lock|wr2rdlock|wait|wait_us|signal|broadcast)\s*\(')
decl_cnt = collections.Counter()
decl_files = collections.defaultdict(set)
guard_cnt = collections.Counter()
manual_cnt = collections.Counter()
guard_total = 0
manual_total = 0
guard_mod = collections.Counter()
manual_mod = collections.Counter()
for dp, dn, fn in os.walk(root):
    for f in fn:
        full = os.path.join(dp, f)
        rel = os.path.relpath(full, root)
        mod = rel.split('/')[1]
        s = open(full, encoding='utf-8', errors='replace').read()
        for t, r in decl_re.items():
            for m in r.finditer(s):
                line_start = s.rfind('\n', 0, m.start()) + 1
                line = s[line_start:s.find('\n', m.start())]
                if re.search(r'\b(class|struct|typedef|using|friend|return|new|delete)\b', line):
                    continue
                if re.search(r'Guard', m.group(1)):
                    continue
                decl_cnt[t] += 1
                decl_files[t].add(rel)
        for m in guard_re.finditer(s):
            guard_cnt[m.group(1).split('::')[-1]] += 1
            guard_total += 1
            guard_mod[mod] += 1
        for m in manual_re.finditer(s):
            manual_cnt[m.group(1)] += 1
            manual_total += 1
            manual_mod[mod] += 1
print('== lock declarations (heuristic: type followed by a name) ==')
for t in LOCK_TYPES:
    print(f'{t:24s} {decl_cnt[t]:5d} files={len(decl_files[t])}')
print('total decls', sum(decl_cnt.values()))
print('\n== RAII guard instantiations by guard type (top 40) ==')
for g, c in guard_cnt.most_common(40):
    print(f'{g:40s} {c:5d}')
print('total guards', guard_total, dict(guard_mod.most_common()))
print('\n== manual member calls .lock()/.unlock()/... (all receivers; includes non-lock classes) ==')
for g, c in manual_cnt.most_common():
    print(f'{g:16s} {c:5d}')
print('total manual', manual_total, dict(manual_mod.most_common()))
