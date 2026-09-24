# Rough split of functions that declare `int ret = OB_SUCCESS;` into those that could take the
# `?` form and those that need the local-ret form. Read-only; approximate (regex over code
# with comments and strings blanked).
import re, subprocess, sys
sys.path.insert(0,'/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/s2-errors')
from calls import strip_comments
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
files=subprocess.run(['git','-C',ROOT,'ls-files','src'],capture_output=True,text=True).stdout.split()
exts=('.h','.hpp','.cpp','.cc','.c','.ipp','.inc')
skip={'src/share/ob_errno.cpp','src/share/ob_errno.h','src/oblib/lib/ob_errno.h','src/share/mysql_errno.h'}
files=[f for f in files if f.endswith(exts) and f not in skip]
decl=re.compile(r'\bint\s+ret\s*=\s*(common::|::oceanbase::common::|oceanbase::common::)?OB_SUCCESS\s*;|\bINIT_SUCC\s*\(\s*ret\s*\)')
dis={
 'after-error block (if (OB_FAIL(ret)) / OB_SUCCESS != ret)': re.compile(r'(OB_FAIL\s*\(\s*ret\s*\)|OB_SUCCESS\s*!=\s*ret\b|\bret\s*!=\s*(common::)?OB_SUCCESS\b)(?!\s*\)\s*\{\s*\})'),
 'code test': re.compile(r'\bOB_(?!SUCCESS\b)[A-Z0-9_]+\s*[!=]=\s*ret\b|\bret\s*[!=]=\s*(common::)?OB_(?!SUCCESS\b)[A-Z0-9_]+|case\s+(common::)?OB_[A-Z0-9_]+\s*:'),
 'reset': re.compile(r'(?<!int )(?<!int  )\bret\s*=\s*(common::)?OB_SUCCESS\s*;'),
 'tmp_ret': re.compile(r'\btmp_ret\b|\bOB_TMP_FAIL\b|\bCOVER_SUCC\b'),
 'check macros': re.compile(r'(?<![A-Za-z0-9_])(CK|OX|OZ|OV|OZX1|OZX2)\s*\('),
}
total=0; clean=0
from collections import Counter
hits=Counter()
for f in files:
    try: s=open(ROOT+'/'+f,errors='replace').read()
    except Exception: continue
    if 'OB_SUCCESS' not in s: continue
    s=strip_comments(s)
    seen=set()
    for m in decl.finditer(s):
        # walk back to the unmatched '{'
        depth=0; i=m.start()-1
        while i>=0:
            c=s[i]
            if c=='}': depth+=1
            elif c=='{':
                if depth==0: break
                depth-=1
            i-=1
        if i<0 or i in seen: continue
        seen.add(i)
        # forward to matching '}'
        depth=0; j=i
        while j<len(s):
            c=s[j]
            if c=='{': depth+=1
            elif c=='}':
                depth-=1
                if depth==0: break
            j+=1
        body=s[i:j+1]
        total+=1
        bad=[k for k,p in dis.items() if p.search(body)]
        for k in bad: hits[k]+=1
        if not bad: clean+=1
print('bodies declaring int ret = OB_SUCCESS:',total)
print('with none of the listed constructs (upper bound for the ? form):',clean, '%.1f%%'%(100*clean/total))
for k,v in hits.most_common(): print('  ',k,v)
