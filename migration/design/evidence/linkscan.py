import subprocess, os, sys, re, json, collections, glob, pickle

B = '/Users/colin/seekdb-dev/ref-834bbee1e/build_release'
NM = '/Users/colin/seekdb-dev/migrate-to-rust/deps/3rd/usr/local/oceanbase/devtools/bin/llvm-nm'
FILT = '/Users/colin/seekdb-dev/migrate-to-rust/deps/3rd/usr/local/oceanbase/devtools/bin/llvm-cxxfilt'
DEP = '/Users/colin/seekdb-dev/migrate-to-rust/deps/3rd/usr/local/oceanbase/deps/devel'
SDK = '/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk/usr/lib'
CACHE = '/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/nmcache.pkl'


def nm(path):
    out = subprocess.run([NM, '-g', '-P', path], capture_output=True, text=True).stdout
    defined, undefined = set(), set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or line.endswith(':'):
            continue
        name, typ = parts[0], parts[1]
        if name.endswith(':'):
            continue
        if typ == 'U':
            undefined.add(name)
        elif typ in 'TDSBCVWIR' or typ in 'tdsb':
            defined.add(name)
    return defined, undefined


def load():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, 'rb'))
    objs = {}
    for p in glob.glob(B + '/**/*.o', recursive=True):
        rel = os.path.relpath(p, B)
        objs[rel] = nm(p)
    archives = {}
    for p in glob.glob(DEP + '/lib/*.a') + glob.glob(DEP + '/lib64/*.a') + glob.glob(DEP + '/lib/vsag_lib/*.a') + glob.glob(DEP + '/lib/grpc/*.a') + glob.glob(DEP + '/lib/sqlite/*.a') + glob.glob(DEP + '/lib64/grpc/*.a'):
        archives[os.path.relpath(p, DEP)] = nm(p)[0]
    system = {}
    for p in ['libc++.tbd', 'libc++abi.tbd', 'libSystem.B.tbd']:
        system[p] = nm(os.path.join(SDK, p))[0]
    data = (objs, archives, system)
    pickle.dump(data, open(CACHE, 'wb'))
    return data


def demangle(names):
    names = list(names)
    if not names:
        return {}
    out = subprocess.run([FILT, '-n'], input='\n'.join(n[1:] if n.startswith('_') else n for n in names), capture_output=True, text=True).stdout.splitlines()
    return dict(zip(names, out))


def provider_index(objs, archives, system, exclude):
    idx = collections.defaultdict(list)
    for o, (d, u) in objs.items():
        if o in exclude:
            continue
        for s in d:
            idx[s].append(('obj', o))
    for a, d in archives.items():
        for s in d:
            idx[s].append(('lib', a))
    for a, d in system.items():
        for s in d:
            idx[s].append(('sys', a))
    return idx


def category(prov):
    kind, where = prov
    if kind == 'sys':
        return 'system:' + where
    if kind == 'lib':
        return 'thirdparty:' + where
    w = where
    if w.startswith('third-party/jemalloc'):
        return 'jemalloc'
    if w.startswith('rust-target'):
        return 'rust'
    m = re.match(r'src/([^/]+)/(?:([^/]+)/)?CMakeFiles/([^/]+)\.dir/(.*)', w)
    if m:
        top = m.group(1)
        sub = m.group(2) or ''
        rest = m.group(4)
        um = re.match(r'Unity/unity_(\w+?)_(\d+)_c', os.path.basename(rest))
        chunk = os.path.basename(rest)
        return 'seekdb:src/%s%s :: %s' % (top, '/' + sub if sub else '', chunk)
    return 'seekdb:' + w
