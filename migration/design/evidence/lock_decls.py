import re, subprocess, collections
files = subprocess.run(['git','ls-files','src'],capture_output=True,text=True).stdout.split('\n')
files = [f for f in files if re.search(r'\.(h|hpp|cpp|cc|ipp|c)$', f)]
types = ['ObSpinLock','ObMutex','SpinRWLock','ObThreadCond','RWLock','ObLatch','ObLatchMutex','TCRWLock','ObBucketLock','ObByteLock','ObQSyncLock','ObBucketQSyncLock','ObSmallSpinLock','ObSmallSpinLockGuard','ObRecursiveMutex','ObSeqLock','ObDRWLock','ObSpinRWLock','ObTCRef','ObMonitor','ObRWLock','ObTCRWLock','ObSpinLockGuard','ObMutexGuard','ObLatchRGuard','ObLatchWGuard','ObLatchMutexGuard','lib::ObMutex','common::ObSpinLock','ObQSync','ObDynamicQSync','ObLightyQueue','ObLinkQueue','ObFixedQueue','ObMsQueue','ObPriorityQueue','ObPriorityQueue2','ObLinkHashMap','ObLinearHashMap','ObConcurrentFIFOAllocator','ObLfFIFOAllocator','ObSliceAlloc','ObVSliceAlloc','ObAtomicList','QClock','RetireStation','HazardRef','ObFutex','pthread_mutex_t','pthread_rwlock_t','pthread_cond_t','pthread_spinlock_t','std::mutex','std::condition_variable','std::shared_mutex','std::thread','ObThreadCondGuard']
decl = collections.Counter(); declfiles = collections.defaultdict(set)
pat = re.compile(r'^\s*(?:mutable\s+)?(?:static\s+)?(?:common::|lib::|oceanbase::common::|oceanbase::lib::)?(' + '|'.join(re.escape(t) for t in types) + r')(?:<[^;]*>)?\s+(\w+)\s*(?:;|\[|\{|=|\()')
for f in files:
    try: txt = open(f, encoding='utf-8', errors='ignore').read()
    except: continue
    for line in txt.split('\n'):
        m = pat.match(line)
        if m and not line.strip().startswith('//') and 'typedef' not in line and 'class ' not in line and 'struct ' not in line:
            decl[m.group(1)] += 1; declfiles[m.group(1)].add(f)
tot = 0
for t, n in decl.most_common():
    print(f'{t:32s} {n:5d} decls in {len(declfiles[t]):4d} files'); tot += n
print('TOTAL', tot)
