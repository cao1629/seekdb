import linkscan, collections, sys, re
objs, archives, system = linkscan.load()
def island_objs(kind):
    if kind=='geo':
        return [o for o in objs if ('ob_share.dir/Unity/unity_ob_share_ob_share_geo_' in o) or ('ob_share.dir/geo/' in o)]
    if kind=='vsag':
        return [o for o in objs if 'unity_oblib_lib_oblib_lib_ob_vector_util_0' in o]
kind=sys.argv[1]
G=island_objs(kind)
D=set(); U=set()
for o in G:
    d,u=objs[o]; D|=d; U|=u
need=U-D
idx=linkscan.provider_index(objs, archives, system, set(G))
bycat=collections.defaultdict(set)
unres=set()
for s in need:
    provs=idx.get(s)
    if not provs:
        unres.add(s); continue
    # prefer seekdb object, then lib, then sys
    provs=sorted(provs, key=lambda p: {'obj':0,'lib':1,'sys':2}[p[0]])
    bycat[linkscan.category(provs[0])].add(s)
print('island objects:', len(G), 'defined:', len(D), 'undefined-needed:', len(need), 'unresolved:', len(unres))
tot=collections.Counter()
for c,s in bycat.items():
    top=c.split(' :: ')[0]
    if top.startswith('thirdparty:'): top='thirdparty:'+top.split(':',1)[1].split('/')[-1]
    tot[top]+=len(s)
for c,n in tot.most_common(): print('%5d %s'%(n,c))
import pickle
pickle.dump((G,need,dict(bycat),unres), open('/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/first_%s.pkl'%kind,'wb'))
print('unresolved sample:', sorted(unres)[:15])
