# Find X-macro include sites: '#define M' ... '#include "f"' ... '#undef M' within a small window.
# Usage: python3 -I xmacro.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
inc_re = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
def_re = re.compile(r'^\s*#\s*define\s+(\w+)')
undef_re = re.compile(r'^\s*#\s*undef\s+(\w+)')
sites = collections.Counter()
macros = collections.defaultdict(set)
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
        for i, l in enumerate(lines):
            m = inc_re.match(l)
            if not m:
                continue
            before = set()
            for j in range(max(0, i - 12), i):
                d = def_re.match(lines[j])
                if d:
                    before.add(d.group(1))
            after = set()
            for j in range(i + 1, min(len(lines), i + 12)):
                u = undef_re.match(lines[j])
                if u:
                    after.add(u.group(1))
            hit = before & after
            if hit:
                sites[m.group(1)] += 1
                macros[m.group(1)].update(hit)
tot = sum(sites.values())
print('x-macro include sites:', tot, 'distinct list files:', len(sites))
for k, v in sites.most_common(25):
    print(v, k, sorted(macros[k])[:4])
