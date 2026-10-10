import re, json, collections
import os; D = os.environ.get('OUT', '.') + '/'
rows = json.load(open(D + 'rows.json'))

# files per commit
files = {}
cur = None
for ln in open(D + 'names.txt', encoding='utf-8', errors='replace'):
    ln = ln.rstrip('\n')
    if ln.startswith('@@'):
        cur = ln[2:]
        files[cur] = []
    elif ln.strip() and cur:
        files[cur].append(ln.strip())

# patch ids (fix commits only)
pid = {}
for ln in open(D + 'fix_patchids.tsv'):
    p, h = ln.rstrip('\n').split('\t')
    pid[h] = p

CPP = re.compile(r'^(src|deps/oblib/src)/.*\.(c|cc|cpp|cxx|h|hh|hpp|hxx|ipp|inl|y|l)$')
DRIFT = re.compile(r'fix drift between internal and public master', re.I)
BUILD = re.compile(r'compil|\bbuild\b|\blink(er|ing)?\b|cmake|bazel|\bpkg\b|package|packaging|\bdeb\b|\brpm\b|\bmsi\b|toolchain|\bci\b|warning|clang|gcc|lto|uninstall|install|systemd|telemetry|license|\bdocs?\b|readme|typo|format', re.I)
TEST = re.compile(r'\bcases?\b|testcases?|\btests?\b|mysqltest|\bfarm\b|unittest|sqlancer', re.I)
PERF = re.compile(r'\bperf\b|performance|\bslow\b|\bcpu\b|latency|throughput|speed ?up|optimi[sz]', re.I)
PLATFORM = re.compile(r'\bmac(os)?\b|\bwindows\b|\bandroid\b|\bwin\b', re.I)

def primary(hits):
    if any(h.startswith('mem:') for h in hits): return 'memory'
    if any(h.startswith('conc:') for h in hits): return 'concurrency'
    if 'crash' in hits: return 'crash-nocause'
    return 'other'

out = []
seen_pid = {}
for x in rows:
    if not x['is_fix_subj'] or DRIFT.search(x['subj']):
        continue
    fl = files.get(x['h'], [])
    cpp = [f for f in fl if CPP.match(f)]
    rust = [f for f in fl if f.startswith('rust/')]
    p = pid.get(x['h'])
    dup_of = None
    if p:
        if p in seen_pid:
            dup_of = seen_pid[p]
        else:
            seen_pid[p] = x['h']
    out.append(dict(h=x['h'], date=x['date'], author=x['author'], subj=x['subj'], s=primary(x['s_hits']), b=primary(x['b_hits']),
                    s_hits=x['s_hits'], b_hits=x['b_hits'], ncpp=len(cpp), nrust=len(rust), nfiles=len(fl), dup_of=dup_of))

# git log is newest-first, so "first seen" = newest; dup_of points to the newer twin. fine for counting.
print('fix-subject commits excl. drift-sync:', len(out))
uniq = [o for o in out if not o['dup_of']]
print('after patch-id dedup:', len(uniq))
prod = [o for o in uniq if o['ncpp'] > 0]
print('of which touch production C/C++ (src/ or deps/oblib/src):', len(prod))
nonprod = [o for o in uniq if o['ncpp'] == 0]
print('not touching production C/C++:', len(nonprod))

def sub_other(o):
    s = o['subj']
    if BUILD.search(s) or PLATFORM.search(s) and re.search(r'compil|build|link', s, re.I): return 'other:build/pkg/ci'
    if TEST.search(s): return 'other:test/case'
    if PERF.search(s): return 'other:perf'
    return 'other:logic/compat'

for label, key in (('SUBJECT-only', 's'), ('SUBJECT+BODY', 'b')):
    c = collections.Counter()
    for o in prod:
        k = o[key]
        if k == 'other':
            k = sub_other(o)
        c[k] += 1
    print(label, 'classification over prod-C++ fix commits:', sorted(c.items()))

json.dump(out, open(D + 'fix2.json', 'w'), ensure_ascii=False)
with open(D + 'fix2.tsv', 'w') as f:
    for o in out:
        k = o['s'] if o['s'] != 'other' else sub_other(o)
        kb = o['b'] if o['b'] != 'other' else sub_other(o)
        f.write('\t'.join(map(str, [o['h'], o['date'], o['dup_of'] or '-', o['ncpp'], o['nrust'], k, kb, ','.join(o['b_hits']), o['subj']])) + '\n')
