import re, subprocess, sys, collections
root = '/Users/colin/seekdb-dev/migrate-to-rust'
files = subprocess.run(['git','-C',root,'ls-files','--','src/*.h','src/*.cpp','src/*.ipp','src/*.hpp','src/*.cc'],capture_output=True,text=True).stdout.split()
head_re = re.compile(r'\b(class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\s*(?:final\s*)?:\s*([^;{]*?)\{', re.S)
bases = {}
where = {}
for f in files:
    try:
        s = open(f'{root}/{f}', encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    s2 = re.sub(r'//[^\n]*', '', s)
    for m in head_re.finditer(s2):
        name = m.group(2); b = m.group(3)
        if '(' in b or len(b) > 400: continue
        bl = [re.sub(r'\b(public|private|protected|virtual)\b','',x).strip() for x in b.split(',')]
        bl = [re.sub(r'^(::)?(oceanbase::)?(common::|lib::|storage::|sql::|share::)*','',x) for x in bl]
        bases.setdefault(name, set()).update(bl)
        line = s2.count('\n', 0, m.start()) + 1
        where.setdefault(name, f'{f}:{line}')
target = sys.argv[1]
# transitive closure
derived = set()
changed = True
while changed:
    changed = False
    for n, bs in bases.items():
        if n in derived: continue
        for b in bs:
            bn = re.sub(r'<.*','',b).strip()
            if bn == target or bn in derived:
                derived.add(n); changed = True; break
print(len(derived))
for n in sorted(derived):
    print(where[n], n)
