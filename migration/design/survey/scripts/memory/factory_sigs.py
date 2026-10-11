# Count parameter lists (possibly multi-line) that contain an ObIAllocator param and a `T *&` out-param:
# the "allocate in callee, return through out-pointer" factory shape.
# Usage: python3 -I factory_sigs.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
PL = re.compile(r'\b(\w+)\s*\(([^(){};]*)\)\s*(const)?\s*(override|final)?\s*[{;:]', re.S)
ALLOC = re.compile(r'\bObIAllocator\s*[&*]\s*\w+')
OUT = re.compile(r'\b[A-Z]\w*(\s*<[^<>]*>)?\s*\*\s*&\s*\w+')
c = collections.Counter()
bymod = collections.Counter()
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding='utf-8', errors='replace').read()
        for m in PL.finditer(s):
            params = m.group(2)
            a = bool(ALLOC.search(params))
            o = bool(OUT.search(params))
            if a:
                c['sig_with_alloc_param'] += 1
            if o:
                c['sig_with_ptr_out_param'] += 1
            if a and o:
                c['sig_alloc_and_out'] += 1
                bymod[rel.split('/')[1]] += 1
print(dict(c))
print(dict(bymod.most_common()))
