import subprocess, re, sys, collections, os
R='/Users/colin/seekdb-dev/migrate-to-rust'
def files(pathspec):
    return subprocess.check_output(['git','-C',R,'ls-files',pathspec]).decode().split()
inc_re=re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]', re.M)
def includes(f):
    try: t=open(os.path.join(R,f),errors='replace').read()
    except: return []
    return inc_re.findall(t)
if __name__=='__main__':
    spec=sys.argv[1]; own=sys.argv[2]
    c=collections.Counter(); by=collections.Counter()
    for f in files(spec):
        for i in includes(f):
            if i.startswith(own): continue
            c[i]+=1
            top=i.split('/')[0] if '/' in i else '<'+('system' if '.' in i or True else '')+'>'
            by[top if '/' in i else 'system/std: '+i]+=0
    groups=collections.defaultdict(list)
    for i,n in c.items():
        parts=i.split('/')
        key='/'.join(parts[:2]) if len(parts)>2 else (parts[0] if len(parts)>1 else 'toplevel')
        groups[key].append((n,i))
    for k in sorted(groups, key=lambda k:-sum(n for n,_ in groups[k])):
        print('%-28s %3d headers %4d include lines' % (k, len(groups[k]), sum(n for n,_ in groups[k])))
