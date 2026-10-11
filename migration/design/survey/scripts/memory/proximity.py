# Proximity heuristics over the stripped mirror:
#  - alloc call sites: is a NULL check / OB_ALLOCATE_MEMORY_FAILED within the same line or next 4 lines?
#  - placement new sites: is an alloc call within the previous 8 lines (alloc+construct idiom)?
#  - explicit destructor sites: is a free/ob_free/reset within the next 3 lines (destroy+free pair)?
# Usage: python3 -I proximity.py <mirror_root>
import os, re, sys, collections, random
root = sys.argv[1]
ALLOC = re.compile(r'(\.|->)alloc(_aligned|_align)?\s*\(|\bob_malloc(_align)?\s*\(')
CHECK = re.compile(r'OB_ISNULL|OB_NOT_NULL|NULL\s*==|nullptr\s*==|==\s*NULL|==\s*nullptr|!=\s*NULL|!=\s*nullptr|NULL\s*!=|nullptr\s*!=|OB_ALLOCATE_MEMORY_FAILED|if\s*\(\s*!\s*\w+\s*\)|if\s*\(\s*\w+\s*\)|abort_unless|OB_ASSERT')
PNEW = re.compile(r'\bnew\s*\((?!\s*std::nothrow)')
DTOR = re.compile(r'(->|\.)~\w+\s*\(\s*\)')
FREE = re.compile(r'(\.|->)free\s*\(|\bob_free\s*\(|\bOB_DELETE\w*\s*\(|\bob_delete\s*\(|(\.|->)(reset|destroy|destory)\s*\(')
c = collections.Counter()
samples = collections.defaultdict(list)
mods = collections.defaultdict(collections.Counter)
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        mod = rel.split('/')[1]
        lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
        for i, l in enumerate(lines):
            if ALLOC.search(l):
                # skip declarations/definitions of alloc functions
                if re.search(r'\bvoid\s*\*\s*alloc\w*\s*\(', l) or re.search(r'\boverride\b', l):
                    continue
                c['alloc_sites'] += 1
                win = '\n'.join(lines[i:i + 5])
                if CHECK.search(win):
                    c['alloc_checked'] += 1
                else:
                    c['alloc_unchecked'] += 1
                    samples['alloc_unchecked'].append('%s:%d' % (rel, i + 1))
            for _ in PNEW.finditer(l):
                c['pnew_sites'] += 1
                back = '\n'.join(lines[max(0, i - 8):i + 1])
                if ALLOC.search(back) or re.search(r'\b(alloc\w*|ob_malloc\w*|malloc|get_str_res_mem|alloc_str_res_mem)\s*\(', back):
                    c['pnew_after_alloc'] += 1
                    mods['pnew_after_alloc'][mod] += 1
                elif re.search(r'\bnew\s*\(\s*this\s*\)', l):
                    c['pnew_this'] += 1
                elif re.search(r'\bnew\s*\(\s*&', l):
                    c['pnew_addr_member'] += 1
                else:
                    c['pnew_other'] += 1
                    samples['pnew_other'].append('%s:%d' % (rel, i + 1))
            for _ in DTOR.finditer(l):
                c['dtor_sites'] += 1
                fwd = '\n'.join(lines[i:i + 4])
                if FREE.search(fwd):
                    c['dtor_then_free'] += 1
                else:
                    c['dtor_alone'] += 1
                    samples['dtor_alone'].append('%s:%d' % (rel, i + 1))
                mods['dtor'][mod] += 1
for k in ['alloc_sites', 'alloc_checked', 'alloc_unchecked', 'pnew_sites', 'pnew_after_alloc', 'pnew_this',
          'pnew_addr_member', 'pnew_other', 'dtor_sites', 'dtor_then_free', 'dtor_alone']:
    print('%-18s %6d' % (k, c[k]))
random.seed(7)
for k in ('alloc_unchecked', 'pnew_other', 'dtor_alone'):
    print('sample', k, random.sample(samples[k], min(12, len(samples[k]))))
