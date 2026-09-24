import glob, os, re, collections, sys, subprocess
B='/Users/colin/seekdb-dev/ref-834bbee1e/build_release'
SRC='/Users/colin/seekdb-dev/ref-834bbee1e/src/'
def deps(dfile):
    t=open(dfile).read().replace('\\\n',' ')
    parts=t.split(':',1)[1].split()
    return [os.path.normpath(p) for p in parts]
def closure(objs):
    s=set()
    for o in objs:
        d=os.path.join(B,o+'.d') if not o.endswith('.d') else os.path.join(B,o)
        if os.path.exists(d):
            s|=set(deps(d))
    return s
def summarize(files, own_prefixes):
    grp=collections.Counter(); lines=collections.Counter(); n=0
    for f in files:
        if f.startswith(SRC):
            rel=f[len(SRC):]
            if any(rel.startswith(p) for p in own_prefixes): key='(island itself)'
            else:
                p=rel.split('/'); key='/'.join(p[:3]) if p[0] in ('oblib','share') and len(p)>3 else '/'.join(p[:2])
        elif '/deps/3rd/' in f or '/deps/devel/' in f:
            m=re.search(r'/include/([^/]+)',f); key='3rd:'+(m.group(1) if m else '?')
        elif '/usr/include' in f or 'SDKs' in f or '/c++/' in f: key='system'
        else: key='other:'+f.split('/')[-2]
        try: lc=sum(1 for _ in open(f,errors='replace'))
        except: lc=0
        grp[key]+=1; lines[key]+=lc
    return grp, lines
if __name__=='__main__':
    kind=sys.argv[1]
    objs=[os.path.relpath(p,B) for p in glob.glob(B+'/**/*.o',recursive=True)]
    if kind=='geo':
        sel=[o for o in objs if ('ob_share.dir/Unity/unity_ob_share_ob_share_geo_' in o) or ('ob_share.dir/geo/' in o)]; own=['share/geo/']
    elif kind=='vsag':
        sel=[o for o in objs if 'unity_oblib_lib_oblib_lib_ob_vector_util_0' in o]; own=['oblib/lib/vector/']
    files=closure(sel)
    grp,lines=summarize(files,own)
    srcf=[f for f in files if f.startswith(SRC)]
    print(kind,'objects',len(sel),'headers+sources in closure',len(files),'under src/',len(srcf),'lines under src/',sum(lines[k] for k in lines if not k.startswith(('3rd:','system','other'))))
    for k,v in sorted(grp.items(), key=lambda kv:-lines[kv[0]]):
        print('%-40s files=%4d lines=%7d'%(k,v,lines[k]))
