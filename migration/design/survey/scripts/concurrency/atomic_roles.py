# Heuristic role classification of ATOMIC_* call sites by macro and by the target's field name.
# Usage: python3 -I atomic_roles.py <stripped_root>
import os, re, sys, collections

root = sys.argv[1]
call_re = re.compile(r'\b(ATOMIC_[A-Z0-9_]+)\s*\(')
SKIP = {'src/oblib/lib/atomic/ob_atomic.h'}
RMW_ARITH = {'ATOMIC_FAA', 'ATOMIC_AAF', 'ATOMIC_FAS', 'ATOMIC_SAF', 'ATOMIC_INC', 'ATOMIC_DEC'}
CAS = {'ATOMIC_BCAS', 'ATOMIC_VCAS', 'ATOMIC_CAS', 'ATOMIC_ADD_TAG', 'ATOMIC_SUB_TAG', 'ATOMIC_ANDF'}
XCHG = {'ATOMIC_TAS', 'ATOMIC_SET'}
LOADS = {'ATOMIC_LOAD', 'ATOMIC_LOAD_ACQ', 'ATOMIC_LOAD_RLX', 'ATOMIC_LOAD64'}
STORES = {'ATOMIC_STORE', 'ATOMIC_STORE_REL', 'ATOMIC_STORE_RLX', 'ATOMIC_STORE64'}

def first_arg(s, i):
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return s[i:j]
            depth -= 1
        elif c == ',' and depth == 0:
            return s[i:j]
        j += 1
    return s[i:j]

ROLE_PATTERNS = [
    ('refcount', re.compile(r'(ref|refcnt|ref_cnt|ref_count|uref|href|hold)', re.I)),
    ('counter/stat', re.compile(r'(cnt|count|num|total|size|sum|bytes|times|stat|hit|miss|seq|_id_?$|idx|pos|push|pop|clock|version|ts|time|epoch|scn|lsn|offset)', re.I)),
    ('flag/state', re.compile(r'(stop|flag|is_|has_|state|status|inited|enable|running|ready|done|mask|mode|lock|locked|valid|alive|need_)', re.I)),
    ('pointer/link', re.compile(r'(next|prev|head|tail|ptr|node|root|link|top|slot|handle|cur_|current)', re.I)),
]
by_kind = collections.Counter()
by_role = collections.Counter()
cross = collections.Counter()
for dp, dn, fn in os.walk(root):
    for f in fn:
        full = os.path.join(dp, f)
        rel = os.path.relpath(full, root)
        if rel in SKIP:
            continue
        s = open(full, encoding='utf-8', errors='replace').read()
        for m in call_re.finditer(s):
            name = m.group(1)
            a = re.sub(r'\s+', '', first_arg(s, m.end()))
            if name in ('ATOMIC_ADD_TAG', 'ATOMIC_SUB_TAG'):
                a = '&flag_'
            fm = re.search(r'([A-Za-z_]\w*)\)*(?:\[[^\]]*\])?\)*$', a)
            fld = fm.group(1) if fm else a
            if name in RMW_ARITH:
                k = 'rmw_arith'
            elif name in CAS:
                k = 'cas'
            elif name in XCHG:
                k = 'xchg'
            elif name in LOADS:
                k = 'load'
            elif name in STORES:
                k = 'store'
            else:
                k = 'other'
            role = 'unknown'
            for r, p in ROLE_PATTERNS:
                if p.search(fld):
                    role = r
                    break
            by_kind[k] += 1
            by_role[role] += 1
            cross[(k, role)] += 1
print('kinds', dict(by_kind))
print('roles', dict(by_role))
for k in ('rmw_arith', 'cas', 'xchg', 'load', 'store'):
    print(k, {r: cross[(k, r)] for r in ['refcount', 'counter/stat', 'flag/state', 'pointer/link', 'unknown']})
