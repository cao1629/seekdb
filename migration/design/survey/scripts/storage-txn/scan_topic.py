#!/usr/bin/env python3
"""Per-subsystem counts for the storage/txn design topic.

Runs over the comment/string-stripped mirror built by
migration/feasibility/scripts/arch/strip.py (same regexes as
migration/feasibility/scripts/arch/unsafe_share.py for the Tier A/B columns,
so numbers are comparable with the feasibility report).

Usage: python3 -I scan_topic.py <mirror_root>
"""
import collections
import os
import re
import sys

EXTS = ('.cpp', '.h', '.c', '.cc', '.hpp', '.ipp', '.inc', '.def', '.hh', '.cxx')

GROUPS = [
    ('memtable/mvcc', 'src/storage/memtable/mvcc'),
    ('memtable(other)', 'src/storage/memtable'),
    ('tx', 'src/storage/tx'),
    ('tx_table', 'src/storage/tx_table'),
    ('tx_storage', 'src/storage/tx_storage'),
    ('multi_data_source', 'src/storage/multi_data_source'),
    ('tablelock', 'src/storage/tablelock'),
    ('concurrency_control', 'src/storage/concurrency_control'),
    ('deadlock', 'src/storage/deadlock'),
    ('meta_mem', 'src/storage/meta_mem'),
    ('tablet', 'src/storage/tablet'),
    ('ls', 'src/storage/ls'),
    ('blocksstable', 'src/storage/blocksstable'),
    ('blockstore', 'src/storage/blockstore'),
    ('slog', 'src/storage/slog'),
    ('slog_ckpt', 'src/storage/slog_ckpt'),
    ('checkpoint', 'src/storage/checkpoint'),
    ('meta_store', 'src/storage/meta_store'),
    ('compaction', 'src/storage/compaction'),
    ('access', 'src/storage/access'),
    ('storage(other)', 'src/storage'),
    ('palf', 'src/logservice/palf'),
    ('logservice(other)', 'src/logservice'),
]

TIER_A = {
    'placement_new': r'\bnew\s*\((?!\s*std::nothrow)',
    'ob_new_macro': r'\b(OB_NEW\w*|MTL_NEW\w*|OB_DELETE\w*|MTL_DELETE\w*)\s*\(',
    'explicit_dtor': r'(->|\.)~\w+\s*\(\s*\)',
    'reinterpret_cast': r'\breinterpret_cast\s*<',
    'c_style_ptr_cast': r'\(\s*(const\s+)?[A-Za-z_][\w:]*\s*\*\s*\)\s*[\w(&]',
    'raw_alloc_call': r'(\.|->)alloc(_aligned|_align)?\s*\(',
    'mem_copy_set': r'(?<![\w.>])(memcpy|MEMCPY|memmove|MEMMOVE|memset|MEMSET)\s*\(',
    'atomics': r'\b(ATOMIC_[A-Z_]+|__sync_\w+|__atomic_\w+)\s*\(',
    'flex_array': r'\w+\s+\w+\s*\[\s*0\s*\]\s*;',
    'asm_simd': r'\b(asm|__asm__)\s*(volatile|__volatile__)?\s*\(|\b_mm(256|512)?_\w+\s*\(|\bv(ld|st)[1-4]q?_\w+\s*\(',
}
TIER_B = {
    'raw_ptr_member': r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*\*+\s*(const\s+)?\w+_\s*(\[[^\]]*\])?\s*;',
    'ptr_ref_slot': r'\b[A-Z]\w*\s*\*\s*&\s*\w+',
    'intrusive': r'\b(ObDLinkBase|ObDList|ObDLinkNode|ObLink|ObDLink|ObLightHashLink|ObSpLinkQueue|ObLinkQueue|ObLinkHashMap|LinkHashNode|LinkHashValue)\b',
    'manual_ref': r'\b(inc_ref\w*|dec_ref\w*|acquire_ctx_ref|release_ctx_ref|inc_ref_cnt|dec_ref_cnt)\s*\(',
}
EXTRA = {
    # topic-specific
    'serialize_macro': r'\b(OB_UNIS_VERSION\w*|OB_SERIALIZE_MEMBER\w*|OB_DEF_SERIALIZE\w*|OB_DEF_DESERIALIZE\w*|OB_DEF_SERIALIZE_SIZE\w*|NEED_SERIALIZE_AND_DESERIALIZE|OB_UNIS_ADD_LEN\w*|OB_UNIS_ENCODE\w*|OB_UNIS_DECODE\w*|LST_DO_CODE|DEFINE_SERIALIZE|DEFINE_DESERIALIZE|DEFINE_GET_SERIALIZE_SIZE)\b',
    'seq_cst_lock': r'\b(ObSpinLock|ObLatch|ObByteLock|ObSpinRWLock|ObLatchMutex|ObThreadCond|ObQSyncLock|SpinRLockGuard|SpinWLockGuard|ObLatchRGuard|ObLatchWGuard|ObMutexGuard|ObSpinLockGuard|ObByteLockGuard|lib::ObMutex|ObRecursiveMutex|ObBucketLock|ObMultiModeBucketLock|ObBucketHashWLockGuard|ObBucketHashRLockGuard)\b',
    'qsync_epoch': r'\b(QClock|RetireStation|HazardList|ObQSync|CriticalGuard|WaitQuiescent|HazardRef|HazardPtr|ObQSyncWrapper|get_qclock|ObTCRWLock|ObEpoch)\b',
    'virtual_fn': r'\bvirtual\b',
}
ALL = {}
ALL.update(TIER_A)
ALL.update(TIER_B)
ALL.update(EXTRA)
RX = {k: re.compile(v) for k, v in ALL.items()}


def group_of(rel):
    for name, prefix in GROUPS:
        if rel == prefix or rel.startswith(prefix + '/'):
            return name
    return None


def main():
    root = sys.argv[1]
    files = collections.Counter()
    lines = collections.Counter()
    sites = collections.defaultdict(collections.Counter)
    afiles = collections.Counter()
    alines = collections.Counter()
    for dp, _, fns in os.walk(os.path.join(root, 'src')):
        for fn in fns:
            if not fn.endswith(EXTS):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            g = group_of(rel)
            if g is None:
                continue
            with open(p, encoding='utf-8', errors='replace') as fh:
                s = fh.read()
            nl = s.count('\n') + 1
            files[g] += 1
            lines[g] += nl
            has_a = False
            for k, r in RX.items():
                n = len(r.findall(s))
                if n:
                    sites[g][k] += n
                    if k in TIER_A:
                        has_a = True
            if has_a:
                afiles[g] += 1
                alines[g] += nl
    cols = list(ALL)
    order = [n for n, _ in GROUPS]
    print('group\tfiles\tlines\ttierA_file%\ttierA_line%\t' + '\t'.join(cols))
    tot = collections.Counter()
    for g in order:
        if not files[g]:
            continue
        row = [g, str(files[g]), str(lines[g]),
               '%.1f' % (100.0 * afiles[g] / files[g]),
               '%.1f' % (100.0 * alines[g] / lines[g])]
        row += [str(sites[g][c]) for c in cols]
        print('\t'.join(row))
        tot['files'] += files[g]
        tot['lines'] += lines[g]
        tot['af'] += afiles[g]
        tot['al'] += alines[g]
        for c in cols:
            tot[c] += sites[g][c]
    row = ['TOTAL', str(tot['files']), str(tot['lines']),
           '%.1f' % (100.0 * tot['af'] / tot['files']),
           '%.1f' % (100.0 * tot['al'] / tot['lines'])]
    row += [str(tot[c]) for c in cols]
    print('\t'.join(row))


if __name__ == '__main__':
    main()
