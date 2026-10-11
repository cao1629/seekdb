# Classes that OWN an arena/allocator member and also hold borrowed members (raw pointers / ObString /
# arena-bound containers): the self-referential shape that plain Rust lifetimes cannot express.
# Usage: python3 -I selfref.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
KW = re.compile(r'\b(class|struct)\s+((?:(?:alignas\s*\([^)]*\)|__attribute__\s*\(\([^)]*\)\)|[A-Z][A-Z0-9_]*)\s+)*)(\w+)\s*(final\s*)?(:(?!:)[^{;()]*(?:\([^)]*\)[^{;()]*)*)?\{')
OWN = re.compile(r'(?m)^\s+(?:common::|lib::)?(ObArenaAllocator|ObSafeArenaAllocator|ObFIFOAllocator|ObMalloc|ObConcurrentFIFOAllocator|ObVSliceAlloc|ObSliceAlloc|ObSmallAllocator|ObReserveArenaAllocator<[^>]*>|MemoryContext|ObWrapperAllocator\w*|ObLocalAllocator|ObQueryAllocator)\s+(\w+_)\s*(\([^;]*\))?\s*;')
BORROW_ALLOC = re.compile(r'(?m)^\s+(?:common::)?ObIAllocator\s*[*&]\s*\w+_\s*;')
STR = re.compile(r'(?m)^\s+(const\s+)?(common::)?ObString\s+\w+_\s*(\[[^\]]*\])?\s*;')
PTR = re.compile(r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*\*+\s*(const\s+)?\w+_\s*(\[[^\]]*\])?\s*;')
FIXED = re.compile(r'(?m)^\s+(common::)?(ObFixedArray|ObList)\s*<[^;]*>\s+\w+_\s*;')


def match_brace(s, i):
    d = 0
    for j in range(i, len(s)):
        if s[j] == '{':
            d += 1
        elif s[j] == '}':
            d -= 1
            if d == 0:
                return j
    return len(s) - 1


def depth1(body):
    out, d = [], 0
    for c in body:
        if c == '{':
            d += 1
            continue
        if c == '}':
            d -= 1
            continue
        if d == 0:
            out.append(c)
    return ''.join(out)


stats = collections.Counter()
bymod = collections.Counter()
examples = []
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.h', '.ipp', '.cpp')):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding='utf-8', errors='replace').read()
        for m in KW.finditer(s):
            ob = m.end() - 1
            cb = match_brace(s, ob)
            d1 = depth1(s[ob + 1:cb])
            own = OWN.findall(d1)
            if not own:
                if BORROW_ALLOC.search(d1):
                    stats['classes_borrowing_allocator_only'] += 1
                continue
            stats['classes_owning_allocator'] += 1
            nstr = len(STR.findall(d1))
            nptr = len(PTR.findall(d1))
            nfix = len(FIXED.findall(d1))
            if nstr or nptr or nfix:
                stats['owning_and_borrowing'] += 1
                bymod[rel.split('/')[1]] += 1
                if len(examples) < 4000:
                    examples.append((nstr + nptr + nfix, rel, s.count('\n', 0, m.start()) + 1, m.group(3), [o[0] for o in own]))
print(dict(stats))
print('owning_and_borrowing by module:', dict(bymod.most_common()))
examples.sort(reverse=True)
for e in examples[:15]:
    print('  %3d %s:%d %s owns=%s' % e)
