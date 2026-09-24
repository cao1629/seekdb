import re, subprocess, os, collections
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
EXTS=('.h','.hpp','.cpp','.cc','.c','.ipp','.inc')
GEN={'src/share/ob_errno.cpp','src/share/ob_errno.h','src/oblib/lib/ob_errno.h','src/share/mysql_errno.h'}
files=[f for f in subprocess.run(['git','-C',ROOT,'ls-files','src'],capture_output=True,text=True).stdout.split('\n') if f.endswith(EXTS) and f not in GEN]
names=set()
for l in open(os.path.join(ROOT,'src/share/ob_errno.def'),encoding='utf-8',errors='replace'):
    m=re.match(r'^DEFINE_(?:ERROR\w*|OTHER_MSG_FMT)\(([^,]+),',l)
    if m: names.add(m.group(1).strip())
ns=r'(?:::)?(?:oceanbase::)?(?:common::)?'
eq=re.compile(r'\b(OB_[A-Z0-9_]+)\s*==\s*ret\b|\bret\s*==\s*'+ns+r'(OB_[A-Z0-9_]+)\b')
ne=re.compile(r'\b(OB_[A-Z0-9_]+)\s*!=\s*ret\b|\bret\s*!=\s*'+ns+r'(OB_[A-Z0-9_]+)\b')
c=collections.Counter(); fset=collections.defaultdict(set)
for f in files:
    for l in open(os.path.join(ROOT,f),encoding='utf-8',errors='replace'):
        if 'ret' not in l: continue
        e=[a or b for a,b in eq.findall(l)]; n=[a or b for a,b in ne.findall(l)]
        e_spec=[x for x in e if x in names and x!='OB_SUCCESS']; n_spec=[x for x in n if x in names and x!='OB_SUCCESS']
        if e_spec: c['ret == specific code']+=1; fset['eq'].add(f)
        if n_spec: c['ret != specific code']+=1; fset['ne'].add(f)
        if e_spec or n_spec: c['either']+=1; fset['either'].add(f)
        if e and all(x=='OB_SUCCESS' for x in e) and not e_spec: c['ret == OB_SUCCESS only']+=1
print(dict(c)); print({k:len(v) for k,v in fset.items()})
