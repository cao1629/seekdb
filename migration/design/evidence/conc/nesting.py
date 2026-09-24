#!/usr/bin/env python3
"""Deepest parenthesis nesting and longest AND/OR/+ chains per statement in the 283 tracked .test files."""
import os, re, subprocess
ROOT='/Users/colin/seekdb-dev/migrate-to-rust'
files=[f for f in subprocess.run(['git','-C',ROOT,'ls-files','tools/deploy/mysql_test'],capture_output=True,text=True).stdout.split() if f.endswith('.test')]
res=[]
for f in files:
    txt=open(os.path.join(ROOT,f),encoding='utf-8',errors='replace').read()
    # crude statement split on ';' at line ends, skip mysqltest commands starting with --
    stmt=[]; line_no=0; start=1
    for i,line in enumerate(txt.split('\n'),1):
        s=line.strip()
        if not stmt and (s.startswith('--') or s.startswith('#') or not s): continue
        if not stmt: start=i
        stmt.append(line)
        if s.endswith(';'):
            st='\n'.join(stmt); stmt=[]
            d=m=0
            q=None
            for ch in st:
                if q:
                    if ch==q: q=None
                    continue
                if ch in '\'"`': q=ch; continue
                if ch=='(':
                    d+=1; m=max(m,d)
                elif ch==')': d-=1
            subq=len(re.findall(r'\(\s*select\b',st,re.I))
            ands=len(re.findall(r'\b(and|or)\b',st,re.I))
            unions=len(re.findall(r'\bunion\b',st,re.I))
            res.append((m,subq,ands,unions,len(st),f,start))
print('statements:',len(res))
for key,name in ((0,'paren depth'),(1,'"(select" count'),(2,'AND/OR count'),(3,'UNION count'),(4,'length')):
    top=sorted(res,key=lambda r:-r[key])[:5]
    print('top by',name)
    for r in top: print('  ',r[key],f'{r[5]}:{r[6]}')
