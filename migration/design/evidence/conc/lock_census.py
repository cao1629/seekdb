#!/usr/bin/env python3
"""Census of lock declarations, lock-guard uses, thread-locals and condition variables in src/.
Comments stripped; #define lines skipped."""
import collections, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from atomic_census import strip_comments, list_files, ROOT
LOCK_TYPES = ['ObLatch','ObLatchMutex','ObMutex','ObSpinLock','SpinRWLock','ObSmallSpinLock','ObPtrSpinLock',
 'TCRWLock','TCRef','ObQSyncLock','ObBucketLock','ObBucketQSyncLock','DRWLock','ObThreadCond','SimpleCond',
 'ObRecursiveMutex','ObMonitor','RWLock','ObRWLock','ObUtilMutex','Cond','ObFutex','ObCond','ObRowLatch','CtxLock',
 'ObLSLock','ObOBJLock','MemtableMgrLock','RingSpinLock','ObTransCond','ObLightyCond','NLock','NCond','DIRWLock',
 'ObByteLock','ObQSync','ObReentrantRWLock','ObBucketLockCond','SlidingCond','ObPxTargetCond','ObSimpleCond',
 'pthread_mutex_t','pthread_rwlock_t','pthread_cond_t','pthread_spinlock_t','std::mutex','std::recursive_mutex',
 'std::shared_mutex','std::condition_variable','std::timed_mutex','std::atomic_flag']
ns = r'(?:(?:oceanbase::)?(?:common|lib|share|storage|memtable|transaction|palf|sql|observer)::)?'
decl_res = {t: re.compile(r'^\s*(?:mutable\s+|static\s+|volatile\s+|thread_local\s+|alignas\([^)]*\)\s+)*' + (re.escape(t) if '::' in t else ns + re.escape(t)) + r'(?:\s*<[^;>]*>)?\s+(?:CACHE_ALIGNED\s+)?[A-Za-z_]\w*(?:\s*\[[^\]]*\])?(?:\s*\([^;]*\)|\s*\{[^;]*\}|\s*=[^;]*)?\s*(?:CACHE_ALIGNED\s*)?;') for t in LOCK_TYPES}
guard_re = re.compile(r'\b((?:std::)?\w*(?:Guard|lock_guard|unique_lock|shared_lock|scoped_lock)\w*)\s*(?:<[^;>]*>)?\s+[A-Za-z_]\w*\s*[\(\{]')
tl_re = re.compile(r'\b(thread_local|__thread|_RLOCAL|RLOCAL|RLOCAL_INLINE|RLOCAL_STATIC|RLOCAL_EXTERN|RLOCAL_INIT|TLOCAL)\b')
tsi_re = re.compile(r'\b(GET_TSI0|GET_TSI|GET_TSI_MULT0|GET_TSI_MULT|ObDITls\s*<)')
latchid_re = re.compile(r'\bObLatchIds::(\w+)')
decls = collections.Counter(); decl_files = collections.defaultdict(set)
guards = collections.Counter(); guard_files = collections.defaultdict(set)
tl = collections.Counter(); tl_files=set(); tl_lines=[]
tsi = collections.Counter(); tsi_files=set()
latch_ids = collections.Counter()
for f in list_files():
    p = os.path.join(ROOT, f)
    try: text = strip_comments(open(p, encoding='utf-8', errors='replace').read())
    except OSError: continue
    for i, line in enumerate(text.split('\n')):
        s = line.strip()
        if s.startswith('#'): continue
        for t, r in decl_res.items():
            if r.match(line):
                decls[t] += 1; decl_files[t].add(f); break
        for m in guard_re.finditer(line):
            g = m.group(1)
            if re.search(r'Lock|Latch|Mutex|Cond|Spin|QSync|Bucket|lock_guard|unique_lock|shared_lock|scoped_lock', g):
                guards[g] += 1; guard_files[g].add(f)
        m = tl_re.search(line)
        if m:
            tl[m.group(1)] += 1; tl_files.add(f); tl_lines.append(f'{f}:{i+1}: {s[:150]}')
        for m in tsi_re.finditer(line):
            tsi[m.group(1).replace(' ','')] += 1; tsi_files.add(f)
        for m in latchid_re.finditer(line):
            latch_ids[m.group(1)] += 1
print('LOCK DECLARATIONS (fields, locals, globals):', sum(decls.values()), 'in', len(set().union(*decl_files.values())), 'files')
for t, c in decls.most_common(): print(f'  {c:5d} {t} ({len(decl_files[t])} files)')
print('LOCK GUARD OBJECTS:', sum(guards.values()), 'in', len(set().union(*guard_files.values())), 'files')
for g, c in guards.most_common(30): print(f'  {c:5d} {g} ({len(guard_files[g])} files)')
print('THREAD-LOCAL DECLARATION LINES:', sum(tl.values()), 'in', len(tl_files), 'files')
for k, c in tl.most_common(): print(f'  {c:5d} {k}')
print('TSI (ObDITls) uses:', sum(tsi.values()), 'in', len(tsi_files), 'files', dict(tsi))
print('ObLatchIds:: references:', sum(latch_ids.values()), 'distinct ids used:', len(latch_ids))
with open(os.path.join(os.path.dirname(__file__), 'thread_local_lines.txt'), 'w') as out:
    out.write('\n'.join(tl_lines) + '\n')
