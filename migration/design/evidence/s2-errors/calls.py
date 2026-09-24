# Scan LOG_USER_{ERROR,WARN,NOTE} and LOG_MYSQL_USER_* calls, compare the argument count
# with the conversions of the code's user-message format. Read-only.
import re, subprocess, json, sys
sys.path.insert(0,'/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/s2-errors')
from catalog import entries, aliases, nargs, convs
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
files=subprocess.run(['git','-C',ROOT,'ls-files','src'],capture_output=True,text=True).stdout.split()
exts=('.h','.hpp','.cpp','.cc','.c','.ipp','.inc')
skip={'src/share/ob_errno.cpp','src/share/ob_errno.h','src/oblib/lib/ob_errno.h','src/share/mysql_errno.h'}
files=[f for f in files if f.endswith(exts) and f not in skip]
call=re.compile(r'\b(LOG_USER_ERROR|LOG_USER_WARN|LOG_USER_NOTE|LOG_MYSQL_USER_ERROR|LOG_MYSQL_USER_WARN|LOG_MYSQL_USER_NOTE|LOG_USER_ERROR_WITH_LINE_COL)\s*\(')
def strip_comments(s):
    out=[];i=0;n=len(s)
    while i<n:
        if s.startswith('//',i):
            j=s.find('\n',i); j=n if j<0 else j
            out.append(' '*(j-i)); i=j
        elif s.startswith('/*',i):
            j=s.find('*/',i+2); j=n if j<0 else j+2
            out.append(re.sub(r'[^\n]',' ',s[i:j])); i=j
        elif s[i]=='"' :
            j=i+1
            while j<n and s[j]!='"':
                j+=2 if s[j]=='\\' else 1
            out.append(s[i:j+1]); i=j+1
        elif s[i]=="'":
            j=i+1
            while j<n and s[j]!="'":
                j+=2 if s[j]=='\\' else 1
            out.append(s[i:j+1]); i=j+1
        else:
            out.append(s[i]); i+=1
    return ''.join(out)
def split_args(s,start):
    depth=0;i=start;args=[];cur=[]
    n=len(s)
    while i<n:
        ch=s[i]
        if ch in '([{': depth+=1; cur.append(ch)
        elif ch in ')]}':
            if depth==0:
                args.append(''.join(cur).strip()); return args,i
            depth-=1; cur.append(ch)
        elif ch=='"':
            j=i+1
            while j<n and s[j]!='"':
                j+=2 if s[j]=='\\' else 1
            cur.append(s[i:j+1]); i=j
        elif ch=="'":
            j=i+1
            while j<n and s[j]!="'":
                j+=2 if s[j]=='\\' else 1
            cur.append(s[i:j+1]); i=j
        elif ch==',' and depth==0:
            args.append(''.join(cur).strip()); cur=[]
        else: cur.append(ch)
        i+=1
    return None,i
from collections import Counter
stats=Counter(); mism=[]; unknown=Counter(); nonconst=[]
for f in files:
    try: s=open(ROOT+'/'+f,errors='replace').read()
    except Exception: continue
    if 'LOG_USER' not in s and 'LOG_MYSQL_USER' not in s: continue
    s=strip_comments(s)
    for m in call.finditer(s):
        # skip macro definitions
        ls=s.rfind('\n',0,m.start())+1
        if s[ls:m.start()].lstrip().startswith('#'): continue
        args,end=split_args(s,m.end())
        if args is None: continue
        macro=m.group(1)
        line=s.count('\n',0,m.start())+1
        code=args[0]
        rest=args[1:]
        if macro=='LOG_USER_ERROR_WITH_LINE_COL': rest=rest[2:]
        if rest==['']: rest=[]
        stats[macro]+=1
        name=code.replace('common::','').replace('::oceanbase::','').replace('oceanbase::','').strip()
        if name in entries: fmt=entries[name]['user']
        elif name in aliases: fmt=aliases[name]['fmt']
        else:
            unknown[name]+=1; nonconst.append((f,line,macro,code)); continue
        want=nargs(fmt)
        if want!=len(rest):
            mism.append((f,line,macro,name,want,len(rest)))
if __name__=="__main__": print(stats, sum(stats.values()))
print('codes not in catalog:',unknown.most_common(10))
print('arg-count mismatches:',len(mism))
for x in mism[:60]: print(' ',x)
json.dump(dict(mism=mism,nonconst=nonconst),open('/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/s2-errors/calls.json','w'))
