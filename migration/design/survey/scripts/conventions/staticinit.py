# Count translation units (object files) in the existing release build that carry dynamic initialisers.
# Reads build_release/src/**/*.o with `nm` (no build performed).
# usage: python3 -I staticinit.py build_release
import collections
import os
import subprocess
import sys

build = sys.argv[1]
objs = []
for dp, dn, fn in os.walk(os.path.join(build, 'src')):
    for f in fn:
        if f.endswith('.o'):
            objs.append(os.path.join(dp, f))
with_init = 0
var_inits = 0
by_mod = collections.Counter()
tot_mod = collections.Counter()
examples = []
for o in sorted(objs):
    out = subprocess.run(['nm', o], capture_output=True, text=True).stdout
    gl = [l for l in out.splitlines() if '__GLOBAL__sub_I_' in l or '_GLOBAL__sub_I_' in l]
    vi = [l for l in out.splitlines() if '__cxx_global_var_init' in l]
    mod = os.path.relpath(o, os.path.join(build, 'src')).split(os.sep)[0]
    tot_mod[mod] += 1
    if gl:
        with_init += 1
        by_mod[mod] += 1
        if len(examples) < 5:
            examples.append((o, gl[0].split()[-1]))
    var_inits += len(vi)
print('object files:', len(objs))
print('object files with a dynamic-initialiser function (_GLOBAL__sub_I_*):', with_init)
print('__cxx_global_var_init symbols (per dynamically-initialised variable, when not inlined):', var_inits)
print('by module (with init / total):', {m: f'{by_mod[m]}/{tot_mod[m]}' for m in tot_mod})
for e in examples:
    print('  e.g.', e)
