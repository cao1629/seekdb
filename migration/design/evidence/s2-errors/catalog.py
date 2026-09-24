# Parse src/share/ob_errno.def with gen_errno.pl's regexes (prefix match) and report
# formats, conversions and aliases. Read-only.
import re, sys, json
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
lines=open(ROOT+'/src/share/ob_errno.def').read().splitlines()
Q=r'("[^"]*")'
pats=[
 ('DEFINE_ERROR', re.compile(r'^DEFINE_ERROR\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q+r',\s*'+Q), 'err_cs'),
 ('DEFINE_ERROR', re.compile(r'^DEFINE_ERROR\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q), 'err'),
 ('DEFINE_ERROR_EXT', re.compile(r'^DEFINE_ERROR_EXT\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q+r',\s*'+Q+r',\s*'+Q), 'ext_cs'),
 ('DEFINE_ERROR_EXT', re.compile(r'^DEFINE_ERROR_EXT\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q), 'ext'),
 ('DEFINE_ERROR_DEP', re.compile(r'^DEFINE_ERROR_DEP\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q+r',\s*'+Q), 'err_cs'),
 ('DEFINE_ERROR_DEP', re.compile(r'^DEFINE_ERROR_DEP\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q), 'err'),
 ('DEFINE_ERROR_EXT_DEP', re.compile(r'^DEFINE_ERROR_EXT_DEP\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q+r',\s*'+Q+r',\s*'+Q), 'ext_cs'),
 ('DEFINE_ERROR_EXT_DEP', re.compile(r'^DEFINE_ERROR_EXT_DEP\(([^,]+),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*'+Q+r',\s*'+Q), 'ext'),
]
other=re.compile(r'^DEFINE_OTHER_MSG_FMT\(([^,]+),\s*([^,]*),\s*'+Q+r'\s*,\s*'+Q)
entries={}
aliases={}
for i,l in enumerate(lines,1):
    m=other.match(l)
    if m:
        aliases[m.group(1)]=dict(line=i,target=m.group(2).strip(),fmt=m.group(3)[1:-1])
        continue
    for macro,p,kind in pats:
        m=p.match(l)
        if m:
            g=m.groups()
            name,code,my,ss,m3=g[0],int(g[1]),g[2].strip(),g[3].strip()[1:-1],g[4][1:-1]
            if kind.startswith('ext'):
                m4=g[5][1:-1]
            else:
                m4=m3
            entries[name]=dict(line=i,code=code,mysql=my,sqlstate=ss,str_error=m3,user=m4,macro=macro)
            break
conv=re.compile(r'%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d+))?(hh|h|ll|l|L|z|j|t)?([diouxXeEfFgGaAcspn%])')
def convs(fmt):
    return [m.group(0) for m in conv.finditer(fmt)]
def nargs(fmt):
    n=0
    for m in conv.finditer(fmt):
        flags,width,prec,length,c=m.groups()
        if c=='%': continue
        n+=1
        if width=='*': n+=1
        if prec=='*': n+=1
    return n
if __name__=='__main__':
    from collections import Counter
    fm=[n for n,e in entries.items() if convs(e['user'])]
    print('entries',len(entries),'with formats in user message',len(fm))
    c=Counter()
    for n in fm:
        for x in convs(entries[n]['user']): c[x]+=1
    print('conversions',c.most_common())
    print('aliases',aliases)
    # str_error with % ?
    print('str_error with %:', [n for n,e in entries.items() if '%' in e['str_error']][:10])
    # plain DEFINE_ERROR whose message has conversions
    print('non-EXT entries with conversions:', [n for n,e in entries.items() if convs(e['user']) and 'EXT' not in e['macro']][:20])
    json.dump(dict(entries=entries,aliases=aliases),open('/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/s2-errors/catalog.json','w'))
