import linkscan, pickle, collections, sys, os, re
objs, archives, system = linkscan.load()
kind=sys.argv[1]
G,need,bycat,unres=pickle.load(open('first_%s.pkl'%kind,'rb'))
# symbol -> seekdb objects defining it (exclude weak duplicates by preferring objects that are not the requester)
idx=collections.defaultdict(list)
for o,(d,u) in objs.items():
    for s in d: idx[s].append(o)
reach=set(G); frontier=list(G)
while frontier:
    o=frontier.pop()
    d,u=objs[o]
    for s in u:
        provs=idx.get(s)
        if not provs: continue
        if any(p in reach for p in provs): continue
        p=provs[0]
        reach.add(p); frontier.append(p)
extra=sorted(reach-set(G))
B=linkscan.B
def srcs(o):
    # unity chunk -> list its sources
    p=os.path.join(B,o[:-2]) if o.endswith('.o') else None
    if p and os.path.exists(p) and p.endswith(('.cxx','.c')):
        return re.findall(r'#include "([^"]+)"', open(p).read())
    return [o]
tot_files=0; tot_lines=0; bytop=collections.Counter(); bylines=collections.Counter()
for o in extra:
    for s in srcs(o):
        tot_files+=1
        try: n=sum(1 for _ in open(s,errors='replace'))
        except: n=0
        tot_lines+=n
        m=re.search(r'/src/([^/]+)/([^/]+)',s)
        key=(m.group(1)+'/'+m.group(2)) if m else ('rust' if 'rust-target' in s else 'jemalloc' if 'jemalloc' in s else s.split('/')[0])
        bylines[key]+=n; bytop[key]+=1
print(kind,'naive object-level closure: objects',len(extra),'source files',tot_files,'lines',tot_lines)
for k,n in bylines.most_common(25): print('%8d lines %5d files %s'%(n,bytop[k],k))
