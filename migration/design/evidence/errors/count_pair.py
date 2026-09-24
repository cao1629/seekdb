import re, subprocess, os, collections
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
EXTS=('.h','.hpp','.cpp','.cc','.c','.ipp','.inc')
GEN={'src/share/ob_errno.cpp','src/share/ob_errno.h','src/oblib/lib/ob_errno.h','src/share/mysql_errno.h'}
files=[f for f in subprocess.run(['git','-C',ROOT,'ls-files','src'],capture_output=True,text=True).stdout.split('\n') if f.endswith(EXTS) and f not in GEN]
ns=r'(?:::)?(?:oceanbase::)?(?:common::)?'
lue=re.compile(r'^\s*(?!//)(?:.*?[;{}]\s*)?\b(LOG_USER_ERROR|LOG_USER_WARN)\s*\(\s*'+ns+r'(OB_[A-Z0-9_]+)')
c=collections.Counter()
for f in files:
    L=open(os.path.join(ROOT,f),encoding='utf-8',errors='replace').read().split('\n')
    for i,l in enumerate(L):
        m=lue.search(l)
        if not m: continue
        mac,code=m.groups()
        win='\n'.join(L[max(0,i-4):i+5])
        same=re.search(r'\bret\s*=\s*'+ns+re.escape(code)+r'\s*;', win) or re.search(r'\b'+re.escape(code)+r'\s*==\s*ret\b|\bret\s*==\s*'+ns+re.escape(code)+r'\b', win)
        other=[x for x in re.findall(r'\bret\s*=\s*'+ns+r'(OB_[A-Z0-9_]+)\s*;', win) if x not in (code,'OB_SUCCESS')]
        reset=re.search(r'\bret\s*=\s*'+ns+r'OB_SUCCESS\s*;', '\n'.join(L[i:i+4]))
        key=mac+(': same code set or tested within 4 lines' if same else (': another code set nearby' if other else ': no code assignment nearby'))
        c[key]+=1
        if reset: c[mac+': followed by ret = OB_SUCCESS within 3 lines']+=1
for k,v in sorted(c.items()): print('%-70s %6d'%(k,v))
