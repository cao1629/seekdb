import re, subprocess, collections
root='/Users/colin/seekdb-dev/migrate-to-rust'
files=[f for f in subprocess.run(['git','-C',root,'ls-files','src'],capture_output=True,text=True).stdout.split() if f.endswith(('.h','.hpp','.cpp','.cc','.ipp','.c','.inc','.def'))]
pats={
 'server_service<T>()': re.compile(r'(?<![a-z_])server_service<[^;()]*?>\s*\(\)'),
 'GCTX': re.compile(r'\bGCTX\b'),
 'GCONF': re.compile(r'\bGCONF\b'),
 'GMEMCONF': re.compile(r'\bGMEMCONF\b'),
 'THIS_WORKER': re.compile(r'\bTHIS_WORKER\b'),
 '::get_instance()': re.compile(r'::get_instance\(\)'),
 'OBSERVER/ObServer::get_instance': re.compile(r'\bOBSERVER\s*(\.|->)|ObServer::get_instance\(\)'),
 'SERVER_MODULE_SCOPE': re.compile(r'\bSERVER_MODULE_SCOPE\b'),
}
excl={'GCTX':'src/share/ob_server_struct.h','GCONF':'src/share/config/ob_server_config.h','GMEMCONF':'src/share/config/ob_server_config.h','THIS_WORKER':'src/oblib/lib/worker.h','SERVER_MODULE_SCOPE':'src/share/rc/ob_server_runtime.h'}
res={k:collections.Counter() for k in pats}
filesper={k:collections.Counter() for k in pats}
for f in files:
    s=open(f'{root}/{f}',encoding='utf-8',errors='replace').read()
    mod=f.split('/')[1]
    for k,p in pats.items():
        n=len(p.findall(s))
        if k in excl and f==excl[k]:
            n -= 1 if k!='SERVER_MODULE_SCOPE' else 0
            if k=='SERVER_MODULE_SCOPE': n=0
        if n>0:
            res[k][mod]+=n; filesper[k][mod]+=1
mods=['oblib','share','logservice','data_plane','query','storage','sql','pl','rootserver','standby','observer']
print('| Pattern | total (files) | ' + ' | '.join(mods)+' |')
for k in pats:
    tot=sum(res[k].values()); tf=sum(filesper[k].values())
    print(f'| {k} | {tot} ({tf}) | ' + ' | '.join(str(res[k].get(m,0)) for m in mods)+' |')
