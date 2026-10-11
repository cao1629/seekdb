# For concurrency-infrastructure headers, count lines and the number of files (outside the
# header's own .h/.cpp/.ipp group) that #include them, using the depmap edge list.
# Usage: python3 -I includers.py <repo>
import os, sys, collections

repo = sys.argv[1]
HEADERS = [
    'src/oblib/lib/lock/ob_latch.h', 'src/oblib/lib/lock/ob_spin_rwlock.h', 'src/oblib/lib/lock/ob_spin_lock.h',
    'src/oblib/lib/lock/ob_mutex.h', 'src/oblib/lib/lock/ob_thread_cond.h', 'src/oblib/lib/lock/ob_futex.h',
    'src/oblib/lib/lock/ob_small_spin_lock.h', 'src/oblib/lib/lock/ob_tc_rwlock.h', 'src/oblib/lib/lock/ob_tc_ref.h',
    'src/oblib/lib/lock/ob_drw_lock.h', 'src/oblib/lib/lock/ob_bucket_lock.h', 'src/oblib/lib/lock/ob_qsync_lock.h',
    'src/oblib/lib/lock/ob_bucket_qsync_lock.h', 'src/oblib/lib/lock/ob_rwlock.h', 'src/oblib/lib/lock/ob_scond.h',
    'src/oblib/lib/lock/ob_monitor.h', 'src/oblib/lib/lock/mutex.h', 'src/oblib/lib/lock/cond.h',
    'src/oblib/lib/lock/ob_recursive_mutex.h', 'src/oblib/lib/lock/ob_lock.h', 'src/oblib/lib/lock/ob_lock_guard.h',
    'src/oblib/lib/queue/ob_link.h', 'src/oblib/lib/queue/ob_link_queue.h', 'src/oblib/lib/queue/ob_lighty_queue.h',
    'src/oblib/lib/queue/ob_fixed_queue.h', 'src/oblib/lib/queue/ob_ms_queue.h', 'src/oblib/lib/queue/ob_co_seq_queue.h',
    'src/oblib/lib/queue/ob_priority_queue.h',
    'src/oblib/lib/hash/ob_link_hashmap.h', 'src/oblib/lib/hash/ob_link_hashmap_deps.h', 'src/oblib/lib/hash/ob_dchash.h',
    'src/oblib/lib/hash/ob_linear_hash_map.h', 'src/oblib/lib/hash/ob_hash.h', 'src/oblib/lib/hash/ob_darray.h',
    'src/oblib/lib/hash/ob_fixed_hash2.h', 'src/oblib/lib/hash/ob_cuckoo_hashmap.h',
    'src/oblib/lib/allocator/ob_retire_station.h', 'src/oblib/lib/allocator/ob_qsync.h', 'src/oblib/lib/allocator/ob_hazard_ref.h',
    'src/oblib/lib/allocator/ob_heavy_ref.h', 'src/oblib/lib/allocator/ob_delay_free_allocator.h',
    'src/oblib/lib/container/ob_ring_array.h', 'src/oblib/lib/container/ob_ext_ring_buffer.h',
    'src/oblib/lib/core_local/ob_core_local_storage.h', 'src/oblib/lib/thread_local/ob_tsi_utils.h',
    'src/oblib/lib/thread_local/ob_tsi_factory.h',
    'src/oblib/lib/thread/thread.h', 'src/oblib/lib/thread/threads.h', 'src/oblib/lib/thread/thread_pool.h',
    'src/oblib/lib/thread/ob_simple_thread_pool.h', 'src/oblib/lib/thread/ob_dynamic_thread_pool.h',
    'src/oblib/lib/thread/ob_map_queue_thread_pool.h', 'src/oblib/lib/thread/ob_async_task_queue.h',
    'src/oblib/lib/thread/ob_dedup_queue.h', 'src/oblib/lib/thread/ob_reentrant_thread.h',
    'src/oblib/lib/thread/ob_adaptive_worker_pool.h', 'src/oblib/lib/task/ob_timer.h', 'src/oblib/lib/task/ob_timer_service.h',
    'src/oblib/lib/worker.h', 'src/oblib/lib/atomic/ob_atomic.h', 'src/oblib/lib/signal/ob_signal_struct.h',
    'src/oblib/lib/signal/ob_signal_utils.h', 'src/share/cache/ob_kvcache_hazard_pointer.h',
    'src/share/cache/ob_kvcache_hazard_domain.h', 'src/share/cache/ob_kvcache_hazard_version.h',
    'src/storage/memtable/mvcc/ob_keybtree.h', 'src/storage/memtable/ob_lock_wait_mgr.h',
]
lines = {}
unit_of = {}
for l in open(os.path.join(repo, 'migration/depmap/nodes.tsv')).read().splitlines()[1:]:
    p, n, crate, unit = l.split('\t')
    lines[p] = int(n)
    unit_of[p] = unit
inc = collections.defaultdict(set)
hdr = open(os.path.join(repo, 'migration/depmap/edges.tsv')).readline().rstrip('\n').split('\t')
for l in open(os.path.join(repo, 'migration/depmap/edges.tsv')).read().splitlines()[1:]:
    cols = l.split('\t')
    rec = dict(zip(hdr, cols))
    src = rec.get('src') or rec.get('from') or cols[0]
    dst = rec.get('dst') or rec.get('to') or cols[1]
    inc[dst].add(src)
print('edges header:', hdr)
for h in HEADERS:
    u = unit_of.get(h)
    group = [p for p in lines if unit_of.get(p) == u] if u else [h]
    glines = sum(lines.get(p, 0) for p in group)
    users = {s for s in inc.get(h, set()) if unit_of.get(s) != u}
    direct_mod = collections.Counter(s.split('/')[1] for s in users)
    print(f'{h:60s} group_lines={glines:6d} includers={len(users):4d} {dict(direct_mod.most_common(5))}')
