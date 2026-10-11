# Inventory of concurrency primitives in the stripped mirror (translation scope = depmap nodes).
# For each identifier: total word-boundary references, files, modules, and refs outside
# the primitive's own home directory (oblib/lib/{lock,queue,thread,allocator,hash,task,...}).
# Usage: python3 -I inventory.py <stripped_root> [group]
import os, re, sys, collections

root = sys.argv[1]
only = sys.argv[2] if len(sys.argv) > 2 else None

GROUPS = collections.OrderedDict()
GROUPS['lock'] = [
    'ObLatch', 'ObLatchMutex', 'ObLatchRGuard', 'ObLatchWGuard', 'ObLatchMutexGuard',
    'SpinRWLock', 'SpinRLockGuard', 'SpinWLockGuard', 'SpinRLockManualGuard',
    'ObSpinLock', 'ObSpinLockGuard', 'ObMutex', 'ObMutexGuard', 'ObMutexGuardWithTimeout',
    'ObRecursiveMutex', 'ObRecursiveMutexGuard',
    'ObThreadCond', 'ObThreadCondGuard', 'ObFutex', 'futex_wait', 'futex_wake', 'futex_hook',
    'ObSmallSpinLock', 'ObByteLock', 'ObByteLockGuard', 'ObPtrSpinLock',
    'TCRWLock', 'TCRef', 'DRWLock', 'ObBucketLock', 'ObBucketRLockGuard', 'ObBucketWLockGuard',
    'ObMultiBucketLockGuard', 'ObBucketWLockAllGuard', 'ObBucketHashRLockGuard', 'ObBucketHashWLockGuard',
    'ObQSyncLock', 'ObBucketQSyncLock', 'ObRWLock', 'ObRLockGuard', 'ObWLockGuard',
    'SimpleCond', 'ObMonitor', 'ObUtilMutex', 'Cond',
    'ObSimpleCond', 'ObCond', 'LWaitCond',
    'pthread_mutex_t', 'pthread_rwlock_t', 'pthread_cond_t', 'pthread_spinlock_t',
    'std::mutex', 'std::condition_variable', 'std::shared_mutex', 'std::lock_guard', 'std::unique_lock',
    'ObLockGuard', 'ObLockGuardWithTimeout', 'lock_guard',
]
GROUPS['lockfree'] = [
    'ObLink', 'ObDLink', 'link_next', 'link_insert', 'link_del', 'ol_insert', 'ol_del', 'ol_get', 'dl_insert', 'dl_del',
    'ObLinkHashMap', 'ObAllocatingLinkHashMap', 'LinkHashValue', 'LinkHashNode', 'DCHash', 'KeyHashNode',
    'ObLinearHashMap', 'ObDArray', 'FixedHash2', 'ObCuckooHashMap',
    'QClock', 'QClockGuard', 'get_global_qclock', 'RetireStation', 'HazardList',
    'ObQSync', 'ObDynamicQSync', 'CriticalGuard', 'WaitQuiescent', 'CriticalGuardDynamic', 'WaitQuiescentDynamic',
    'HazardRef', 'HazardHandle', 'RetireListHandle', 'HazardNode', 'ObHeavyRef', 'ExternalRef',
    'ObLinkQueue', 'ObSpLinkQueue', 'ObSpScLinkQueue', 'ObSimpleLinkQueue', 'ObLightyQueue', 'LightyQueue',
    'ObFixedQueue', 'ObOrderedFixedQueue', 'ObMsQueue', 'ObCoSeqQueue', 'ObPriorityQueue', 'ObPriorityQueue2',
    'ObSeqQueue', 'ObExtendibleRingBuffer', 'ObFixedSizeBlockAllocator',
    'ObConcurrentFIFOAllocator', 'ObLfFIFOAllocator', 'ObSliceAlloc', 'ObVSliceAlloc', 'ObBlockAllocMgr',
    'ObSmallAllocator', 'op_alloc', 'op_free', 'op_reclaim_alloc', 'op_reclaim_free',
    'ObCoreLocalStorage', 'GET_TSI', 'GET_TSI_MULT', 'ObTCFactory', 'thread_local', 'RLOCAL', 'RLOCAL_INLINE', '_RLOCAL', 'RLOCAL_EXTERN', 'RLOCAL_STATIC',
    'get_itid', 'get_max_itid', 'CACHE_ALIGNED',
    'ObSpScLinkQueue', 'ObTCRef',
]
GROUPS['thread'] = [
    'TG_DEF', 'TG_CREATE', 'TG_CREATE_TENANT', 'TG_START', 'TG_STOP', 'TG_WAIT', 'TG_DESTROY', 'TG_SCHEDULE',
    'TG_CANCEL', 'TG_CANCEL_R', 'TG_TASK_EXIST', 'TG_PUSH_TASK', 'TG_SET_RUNNABLE', 'TG_SET_HANDLER',
    'TG_SET_RUNNABLE_AND_START', 'TG_SET_HANDLER_AND_START', 'TG_REENTRANT_LOGICAL_START', 'TG_REENTRANT_LOGICAL_STOP',
    'TG_SET_ADAPTIVE_STRATEGY', 'TG_GET_QUEUE_NUM', 'TG_WAIT_ONLY', 'TG_STOP_R', 'TG_WAIT_R', 'TG_DESTROY_R',
    'TG_SCHEDULE_REPEAT', 'TG_CANCEL_TASK', 'TG_CANCEL_ALL',
    'ObThreadPool', 'ThreadPool', 'Threads', 'lib::Threads', 'lib::Thread', 'ObSimpleThreadPool', 'ObSimpleThreadPoolBase',
    'ObDynamicThreadPool', 'ObMapQueueThreadPool', 'ObAsyncTaskQueue', 'ObDedupQueue', 'ObReentrantThread',
    'ObQueueThread', 'ObAdaptiveWorkerPool', 'ObTimer', 'ObTimerTask', 'ObTimerService', 'ObTimerMonitor',
    'THIS_WORKER', 'Worker', 'ObThWorker', 'ObTenant', 'ObMultiTenant', 'ObPxPool', 'ObPxPools',
    'pthread_create', 'std::thread', 'pthread_join', 'pthread_self', 'GETTID', 'gettid',
    'ObThreadLease', 'ObDynamicThreadTask', 'ObBalanceFilter', 'ObPriorityScheduler',
    'lib::set_thread_name', 'set_thread_name', 'lib::CoKey', 'run1', 'has_set_stop',
    'ob_pthread_create', 'ob_pthread_join', 'ObLinkTask', 'IRunWrapper', 'ObPThread',
]
GROUPS['signal'] = [
    'sigsetjmp', 'siglongjmp', 'setjmp', 'longjmp', 'sigaction', 'signal', 'SIGSEGV', 'SIGBUS', 'SIGABRT',
    'SIGPIPE', 'SIGTERM', 'SIGINT', 'SIGUSR1', 'SIGUSR2', 'SIGHUP', 'SIGQUIT', 'SIGPROF', 'SIGCHLD', 'SIGFPE', 'SIGILL',
    'pthread_kill', 'pthread_sigmask', 'sigprocmask', 'tgkill', 'raise', 'kill', 'sigwait', 'signalfd', 'sigaltstack',
    'install_ob_signal_handler', 'ob_signal_handler', 'coredump_cb', 'SIG_STACK_TRACE', 'SIG_DUMP_MEMORY',
    'MAX_SIGNAL_NUM', 'ObSigHandlerCtx', 'ObSignalHandlerGuard', 'do_with_crash_restore', 'ob_signal_struct',
]
GROUPS['uservis'] = [
    'OB_TRY_LOCK_ROW_CONFLICT', 'OB_ERR_EXCLUSIVE_LOCK_CONFLICT', 'OB_ERR_SHARED_LOCK_CONFLICT',
    'OB_ERR_EXCLUSIVE_LOCK_CONFLICT_NOWAIT', 'OB_DEAD_LOCK', 'OB_TRANS_KILLED', 'OB_TIMEOUT', 'OB_TRANS_TIMEOUT',
    'OB_TRANS_STMT_TIMEOUT', 'OB_ERR_QUERY_INTERRUPTED', 'OB_ERR_LOCK_WAIT_TIMEOUT', 'ObLockWaitMgr',
    'ObDeadLockDetectorMgr', 'ObLCLNode', 'ObDeadLockDetectorAdapter', 'MTL_IS_PRIMARY_TENANT',
    'ob_trx_lock_timeout', 'innodb_lock_wait_timeout', 'ob_query_timeout', 'ob_trx_timeout', 'ob_trx_idle_timeout',
    'SYS_VAR_OB_TRX_LOCK_TIMEOUT', 'SYS_VAR_INNODB_LOCK_WAIT_TIMEOUT', 'OB_LOCK_WAIT_TIMEOUT',
]

def scan(ids):
    pats = {}
    for i in ids:
        esc = re.escape(i)
        pats[i] = re.compile(r'(?<![\w:])' + esc + r'(?!\w)') if '::' in i else re.compile(r'(?<![\w])' + esc + r'(?!\w)')
    refs = collections.Counter()
    files = collections.defaultdict(set)
    mods = collections.defaultdict(collections.Counter)
    for dp, dn, fn in os.walk(root):
        for f in fn:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root)
            parts = rel.split('/')
            mod = parts[1]
            s = open(full, encoding='utf-8', errors='replace').read()
            for i, p in pats.items():
                c = len(p.findall(s))
                if c:
                    refs[i] += c
                    files[i].add(rel)
                    mods[i][mod] += c
    return refs, files, mods

for g, ids in GROUPS.items():
    if only and g != only:
        continue
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    refs, files, mods = scan(seen)
    print(f'===== {g} =====')
    print(f'{"identifier":36s} {"refs":>6s} {"files":>6s}  modules')
    for i in seen:
        md = ', '.join(f'{m}:{c}' for m, c in mods[i].most_common(6))
        print(f'{i:36s} {refs[i]:6d} {len(files[i]):6d}  {md}')
