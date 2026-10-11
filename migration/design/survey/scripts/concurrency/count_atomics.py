# Count atomic-related constructs in the stripped mirror (translation scope only).
# Usage: python3 -I count_atomics.py <stripped_root>
import os, re, sys, collections

root = sys.argv[1]
PATS = {
    'ATOMIC_macro': re.compile(r'\b(ATOMIC_[A-Z0-9_]+)\s*\('),
    'sync_builtin': re.compile(r'\b(__sync_[a-z_]+)\s*\('),
    'atomic_builtin': re.compile(r'\b(__atomic_[a-z_]+)\s*\('),
    'std_atomic_decl': re.compile(r'\bstd::atomic\s*<|\bstd::atomic_(?:bool|int|uint|int64_t|uint64_t|flag|llong|ullong)\b'),
    'memory_order': re.compile(r'\b(std::)?memory_order_([a-z_]+)'),
    'barrier': re.compile(r'\b(MEM_BARRIER|WEAK_BARRIER|__COMPILER_BARRIER|PAUSE|CPU_BARRIER|COMPILER_BARRIER|std::atomic_thread_fence|std::atomic_signal_fence|__sync_synchronize)\s*\('),
    'asm_memory_clobber': re.compile(r'asm\s+(?:volatile|__volatile__)\s*\(\s*""\s*:::\s*""\s*\)'),
    'volatile_kw': re.compile(r'\bvolatile\b'),
}
SKIP = {'src/oblib/lib/atomic/ob_atomic.h'}
by_name = collections.Counter()
by_kind = collections.Counter()
by_kind_mod = collections.defaultdict(collections.Counter)
files_with = collections.defaultdict(set)
for dp, dn, fn in os.walk(root):
    for f in fn:
        full = os.path.join(dp, f)
        rel = os.path.relpath(full, root)
        if rel in SKIP:
            continue
        mod = '/'.join(rel.split('/')[:2])
        s = open(full, encoding='utf-8', errors='replace').read()
        # drop #define lines' macro names for ATOMIC_ definitions so we count uses only
        for kind, pat in PATS.items():
            for m in pat.finditer(s):
                by_kind[kind] += 1
                by_kind_mod[kind][mod] += 1
                files_with[kind].add(rel)
                if kind in ('ATOMIC_macro', 'sync_builtin', 'atomic_builtin', 'barrier'):
                    by_name[(kind, m.group(1))] += 1
                if kind == 'memory_order':
                    by_name[(kind, m.group(2))] += 1
print('== totals by kind (uses, files) ==')
for k in PATS:
    print(f'{k:20s} {by_kind[k]:6d}  files={len(files_with[k])}')
print('\n== by name ==')
for (k, n), c in sorted(by_name.items(), key=lambda x: (x[0][0], -x[1])):
    print(f'{k:16s} {n:28s} {c:6d}')
print('\n== by module ==')
for k in ('ATOMIC_macro', 'sync_builtin', 'atomic_builtin', 'std_atomic_decl', 'barrier', 'volatile_kw'):
    print(k, dict(by_kind_mod[k].most_common()))
