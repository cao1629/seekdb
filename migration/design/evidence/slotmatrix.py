import re, subprocess, collections
root='/Users/colin/seekdb-dev/migrate-to-rust'
files=[f for f in subprocess.run(['git','-C',root,'ls-files','src'],capture_output=True,text=True).stdout.split() if f.endswith(('.h','.hpp','.cpp','.cc','.ipp'))]
look=re.compile(r'(?<![a-z_])server_service<\s*([^;()]*?)>\s*\(\)')
decl=re.compile(r'^\s*(?:class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\b[^;]*$', re.M)
where={}
texts={}
for f in files:
    s=open(f'{root}/{f}',encoding='utf-8',errors='replace').read(); texts[f]=s
    if f.endswith('.h'):
        for m in decl.finditer(s):
            where.setdefault(m.group(1), set()).add(f.split('/')[1] if f.split('/')[1] not in ('query','data_plane') else f.split('/')[1])
mat=collections.Counter(); unknown=collections.Counter(); upward=collections.Counter()
order=['oblib','share','logservice','data_plane','query','storage','sql','pl','standby','rootserver','observer']
rank={m:i for i,m in enumerate(order)}
for f,s in texts.items():
    cm=f.split('/')[1]
    for m in look.finditer(s):
        t=re.sub(r'\s+','',m.group(1)); name=t.split('::')[-1]
        if name.startswith('ObServerObjectPool'): tm='oblib'
        else:
            mods=where.get(name)
            if not mods: unknown[name]+=1; continue
            tm=sorted(mods, key=lambda x: rank.get(x,99))[0] if len(mods)>1 else next(iter(mods))
        mat[(cm,tm)]+=1
        if rank.get(tm,99)>rank.get(cm,99): upward[(cm,tm,name)]+=1
callers=sorted({c for c,_ in mat}, key=lambda x: rank.get(x,99))
targets=sorted({t for _,t in mat}, key=lambda x: rank.get(x,99))
print('| caller \\ service defined in | ' + ' | '.join(targets) + ' |')
print('|' + '---|'*(len(targets)+1))
for c in callers:
    print(f'| {c} | ' + ' | '.join(str(mat.get((c,t),0)) for t in targets) + ' |')
print('unknown', unknown.most_common(10))
tot_up=sum(upward.values()); print('upward lookups (caller in lower module than service):', tot_up)
agg=collections.Counter()
for (c,t,n),v in upward.items(): agg[(c,t)]+=v
print(agg.most_common())
for (c,t,n),v in sorted(upward.items(), key=lambda x:-x[1])[:25]: print(v,c,'->',t,n)
