import re, subprocess, os, collections
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
EXTS=('.h','.hpp','.cpp','.cc','.c','.ipp','.inc')
GEN={'src/share/ob_errno.cpp','src/share/ob_errno.h','src/oblib/lib/ob_errno.h','src/share/mysql_errno.h'}
files=[f for f in subprocess.run(['git','-C',ROOT,'ls-files','src'],capture_output=True,text=True).stdout.split('\n') if f.endswith(EXTS) and f not in GEN]
ns=r'(?:::)?(?:oceanbase::)?(?:common::)?'
P={
 'if (OB_SUCC(ret))': re.compile(r'\bif\s*\(\s*OB_SUCC\s*\(\s*ret\s*\)\s*\)'),
 'if (OB_FAIL(ret)) / if (OB_FAIL(ret) && ...': re.compile(r'\bif\s*\(\s*OB_FAIL\s*\(\s*ret\s*\)'),
 'if (OB_SUCCESS != ret)': re.compile(r'\bif\s*\(\s*(?:OB_UNLIKELY\s*\(\s*)?'+ns+r'OB_SUCCESS\s*!=\s*ret\b'),
 'reset line with an ignore/overwrite comment': re.compile(r'^\s*ret\s*=\s*'+ns+r'OB_SUCCESS\s*;\s*//.*(ignore|overwrite|cover|reset|not care|skip|continue)', re.I),
 'reset line (any)': re.compile(r'^\s*ret\s*=\s*'+ns+r'OB_SUCCESS\s*;'),
 'int ret = OB_SUCCESS; inside void function (approx: void fn above)': None,
}
cnt=collections.Counter(); files_hit=collections.defaultdict(set)
# reset classification: look back 3 lines for a comparison with a specific code / an OB_FAIL
back_code=re.compile(r'\b(OB_[A-Z0-9_]+)\s*==\s*ret\b|\bret\s*==\s*'+ns+r'(OB_[A-Z0-9_]+)\b')
cls=collections.Counter()
for f in files:
    try: L=open(os.path.join(ROOT,f),encoding='utf-8',errors='replace').read().split('\n')
    except Exception: continue
    for i,l in enumerate(L):
        for k,rx in P.items():
            if rx is not None and rx.search(l):
                cnt[k]+=1; files_hit[k].add(f)
        if P['reset line (any)'].search(l):
            win='\n'.join(L[max(0,i-3):i])
            codes=[a or b for a,b in back_code.findall(win)]
            codes=[c for c in codes if c!='OB_SUCCESS']
            if codes:
                cls['after a test for a specific code']+=1
            elif re.search(r'OB_FAIL\s*\(|OB_SUCCESS\s*!=|OB_TMP_FAIL|!=\s*'+ns+r'OB_SUCCESS', win):
                cls['after a generic failure test (error ignored)']+=1
            else:
                cls['other (initialisation, loop reset, ...)']+=1
for k,v in cnt.items(): print('%-50s %7d lines %5d files'%(k,v,len(files_hit[k])))
print('reset lines by what precedes them (3-line window):', dict(cls))
