import re, subprocess, sys, collections
root = '/Users/colin/seekdb-dev/migrate-to-rust'
files = subprocess.run(['git','-C',root,'ls-files','src'],capture_output=True,text=True).stdout.split()
files = [f for f in files if f.endswith(('.h','.hpp','.cpp','.cc','.ipp','.c','.inc'))]
bases = sys.argv[1:]
pat = re.compile(r'\b(class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\s*(?:final\s*)?:\s*([^{;]*)\{', re.S)
res = collections.defaultdict(list)
for f in files:
    try: s = open(f'{root}/{f}', encoding='utf-8', errors='replace').read()
    except Exception: continue
    s2 = re.sub(r'//[^\n]*', '', s)
    s2 = re.sub(r'/\*.*?\*/', '', s2, flags=re.S)
    for m in pat.finditer(s2):
        clause = m.group(3)
        for b in bases:
            if re.search(r'(public|protected|private)\s+(virtual\s+)?([\w:]*::)?' + re.escape(b) + r'\b', clause):
                line = s2[:m.start()].count('\n') + 1
                res[b].append((f, line, m.group(2)))
for b in bases:
    print(b, len(res[b]))
    mods = collections.Counter(f.split('/')[1] for f,_,_ in res[b])
    print('  modules:', dict(mods.most_common()))
if len(bases)==1:
    for f,l,c in res[bases[0]]: print(f'{f}:{l} {c}')
