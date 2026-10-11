# Estimate C++ function overloading from the existing release build's symbol tables (no build performed).
# Defined text symbols from build_release/src/**/*.o, demangled with c++filt; groups by qualified name
# without the parameter list.  Inline/header-only functions that were not emitted are not seen.
# usage: python3 -I overloads.py build_release
import collections
import os
import re
import subprocess
import sys

build = sys.argv[1]
syms = set()
for dp, dn, fn in os.walk(os.path.join(build, 'src')):
    for f in fn:
        if f.endswith('.o'):
            out = subprocess.run(['nm', '-U', os.path.join(dp, f)], capture_output=True, text=True).stdout
            for l in out.splitlines():
                parts = l.split()
                if len(parts) >= 3 and parts[1] in ('T', 't'):
                    syms.add(parts[2])
mangled = sorted(s for s in syms if s.startswith('__Z') or s.startswith('_Z'))
dem = subprocess.run(['c++filt'], input='\n'.join(mangled), capture_output=True, text=True).stdout.splitlines()
groups = collections.defaultdict(set)
tmpl = 0
for d in dem:
    if not d or '(' not in d:
        continue
    if d.startswith(('vtable', 'typeinfo', 'guard variable', 'non-virtual thunk', 'virtual thunk', 'construction vtable')):
        continue
    # split name and params at the top-level '(' that starts the parameter list
    depth = 0
    cut = None
    for i, ch in enumerate(d):
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        elif ch == '(' and depth == 0:
            cut = i
            break
    if cut is None:
        continue
    name = d[:cut]
    params = d[cut:]
    params = re.sub(r'\s+const$', ' const', params)
    if re.search(r'<.*>$', name):
        tmpl += 1
    if 'oceanbase::' not in name:
        continue
    if '::operator' in name or '{lambda' in name or 'anonymous namespace' in name:
        continue
    if '<' in name:
        continue  # template instantiations: not source-level overloads
    base = name
    groups[base].add(params)
fn_names = len(groups)
over = {k: v for k, v in groups.items() if len(v) >= 2}
ctor = {k: v for k, v in over.items() if re.search(r'::(~?)(\w+)::\1?\2$', k) or k.split('::')[-1] == k.split('::')[-2]}
print('demangled oceanbase function names (defined, out-of-line):', fn_names)
print('names with >=2 distinct parameter lists (overloaded):', len(over), f'({100.0 * len(over) / max(fn_names, 1):.1f}%)')
print('  of which constructors:', len(ctor))
print('  total overloads in those sets:', sum(len(v) for v in over.values()))
for k, v in sorted(over.items(), key=lambda kv: -len(kv[1]))[:10]:
    print(f'  {len(v):3d} {k}')
