import subprocess, os, collections, re, sys
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
MAP='/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/rulebook/crates/crates-design.tsv'
files=subprocess.run(['git','-C',ROOT,'ls-files','src','tools/ob_error/src'],capture_output=True,text=True).stdout.split()
EXT={'.h','.cpp','.c','.cc','.ipp','.hpp','.cxx','.hxx'}
files=[f for f in files if os.path.splitext(f)[1] in EXT]
rows=[]
for line in open(MAP):
    if line.startswith('#') or not line.strip(): continue
    parts=line.rstrip('\n').split('\t')
    rows.append((parts[0],parts[1],parts[2] if len(parts)>2 else ''))
alldirs=set()
for f in subprocess.run(['git','-C',ROOT,'ls-files'],capture_output=True,text=True).stdout.split():
    d=os.path.dirname(f)
    while d:
        alldirs.add(d); d=os.path.dirname(d)
def isdir(p): return p in alldirs
def match(f):
    best=None
    for pre,crate,dv in rows:
        if isdir(pre):
            ok=f.startswith(pre+'/')
        else:
            ok=f.startswith(pre)
        if ok and (best is None or len(pre)>len(best[0])): best=(pre,crate,dv)
    return best
out=[]
nomatch=[]
for f in files:
    m=match(f)
    if not m: nomatch.append(f); continue
    pre,crate,dv=m
    base=pre if isdir(pre) else os.path.dirname(pre)
    sub=os.path.dirname(f)[len(base):].strip('/')
    d='/'.join(x for x in [dv,sub] if x)
    name=os.path.basename(f)
    stem=name[:name.rfind('.')]
    if stem in ('lib','main','mod'): stem+='_'
    tgt=f"rust/{crate}/src/{d+'/' if d else ''}{stem}.rs"
    out.append((f,crate,d,stem,tgt))
print('files',len(files),'nomatch',len(nomatch))
for x in nomatch[:20]: print('  NOMATCH',x)
by=collections.defaultdict(list)
for f,crate,d,stem,tgt in out: by[tgt].append(f)
coll=0
api=re.compile(r'^src/(query/api|data_plane/api)/')
lines=[]
for tgt,fs in sorted(by.items()):
    dirs=set(os.path.dirname(x) for x in fs)
    if len(dirs)>1:
        coll+=1
        lines.append(tgt+'\t'+' '.join(fs))
print('targets',len(by),'multi-dir targets',coll)
open('/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/review-b/collisions.txt','w').write('\n'.join(lines)+'\n')
# case-insensitive
ci=collections.defaultdict(set)
for tgt in by: ci[tgt.lower()].add(tgt)
print('case collisions',[v for v in ci.values() if len(v)>1][:10])
# module vs dir collisions within crate: a target file X.rs and a dir X/ in same parent
tdirs=set()
for tgt in by:
    p=os.path.dirname(tgt)
    while p.count('/')>=3:
        tdirs.add(p); p=os.path.dirname(p)
mc=[t for t in by if t[:-3] in tdirs]
print('file-vs-dir collisions',len(mc)); [print('  ',x) for x in mc[:40]]
# invalid identifiers
kw=set('as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn abstract become box do final macro override priv typeof unsized virtual yield try gen'.split())
bad=collections.Counter()
for tgt in by:
    comps=tgt.split('/')[3:]
    comps[-1]=comps[-1][:-3]
    for c in comps:
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$',c): bad['invalid:'+c]+=1
        elif c in kw: bad['keyword:'+c]+=1
print('bad names',bad.most_common(40))
import pickle
pickle.dump(out,open('/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/review-b/targets.pkl','wb'))
