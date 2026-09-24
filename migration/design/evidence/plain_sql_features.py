# Classify the 128 plain-SQL cases (migration/judge/lists/plain-sql.txt) by features that need
# bootstrap pieces beyond a minimal catalog. Follows --source includes. Run from the repo root.
import os, re, glob, collections, sys
names=[l.strip() for l in open('migration/judge/lists/plain-sql.txt') if l.strip() and not l.startswith('#')]
base='tools/deploy/mysql_test'
tests={}
for p in glob.glob(base+'/**/*.test', recursive=True):
    rel=os.path.relpath(p, base); parts=rel.split('/')
    if parts[0]=='t': nm=parts[1][:-5]
    elif parts[0]=='test_suite': nm=parts[1]+'.'+parts[3][:-5]
    else: continue
    tests[nm]=p
def src_text(p, seen=None):
    seen = seen if seen is not None else set()
    if p in seen or not os.path.exists(p): return ''
    seen.add(p)
    t=open(p,encoding='utf-8',errors='replace').read()
    out=[t]
    for m in re.finditer(r'^\s*(?:--)?source\s+([^\s;]+)', t, re.M|re.I):
        inc=m.group(1)
        for cand in (os.path.join(base,inc), os.path.join(os.path.dirname(p),inc), os.path.join(base,'include',os.path.basename(inc))):
            if os.path.exists(cand):
                out.append(src_text(cand, seen)); break
    return '\n'.join(out)
feat={
 'show/desc (virtual tables)':re.compile(r'^\s*(show|desc|describe)\b',re.I|re.M),
 'information_schema':re.compile(r'information_schema',re.I),
 'view':re.compile(r'create\s+(or\s+replace\s+)?(algorithm\s*=\s*\w+\s+)?(definer\s*=\s*\S+\s+)?(sql\s+security\s+\w+\s+)?view',re.I),
 'user PL':re.compile(r'create\s+(definer\s*=\s*\S+\s+)?(procedure|function|trigger)\b',re.I),
 'sys package':re.compile(r'\bdbms_\w+\.',re.I),
 'user/grant':re.compile(r'create\s+user|\bgrant\b|\brevoke\b',re.I),
 'alter table':re.compile(r'alter\s+table',re.I),
 'partition':re.compile(r'partition\s+by',re.I),
 'set global':re.compile(r'set\s+(@@)?global|set\s+global',re.I),
}
blocking=['show/desc (virtual tables)','information_schema','view','user PL','sys package']
per=collections.Counter(); free=[]
for n in names:
    t=src_text(tests[n])
    hits=[k for k,r in feat.items() if r.search(t)]
    per.update(hits)
    if not any(h in blocking for h in hits): free.append(n)
for k,v in per.most_common(): print(f'{v:4d}  {k}')
print('cases with none of the blocking features:', len(free))
if len(sys.argv) > 1:
    open(sys.argv[1],'w').write('\n'.join(free)+'\n')
