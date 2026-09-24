#!/usr/bin/env python3
"""Heuristic: for each (file stem, field name) targeted by ATOMIC_*, count lines in the
stem's own files that mention the field outside an ATOMIC_* call. Declarations, constructor
init lists, K()/K_() log arguments and TO_STRING_KV lines are counted separately."""
import collections, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from atomic_census import strip_comments, ROOT
rows=[l.rstrip('\n').split('\t') for l in open(os.path.join(os.path.dirname(__file__),'atomic_names.tsv'))]
pairs=collections.defaultdict(set)
for name, loc, macro, el, tm, addr in rows:
    f=loc.rsplit(':',1)[0]
    stem=re.sub(r'\.(h|hpp|cpp|cc|c|ipp|inc|def)$','',f)
    if name.endswith('_'):
        pairs[stem].add(name)
cache={}
def stem_files(stem):
    out=[]
    for ext in ('.h','.hpp','.cpp','.cc','.ipp','.inc'):
        p=os.path.join(ROOT,stem+ext)
        if os.path.exists(p): out.append(p)
    return out
plain=collections.Counter(); logonly=collections.Counter(); decl=collections.Counter()
mixed_pairs=0; total_pairs=0; plain_lines_total=0; examples=[]
ATOM=re.compile(r'\bATOMIC_[A-Z_0-9]+\s*\(')
for stem, names in pairs.items():
    texts=[]
    for p in stem_files(stem):
        if p not in cache:
            cache[p]=strip_comments(open(p,encoding='utf-8',errors='replace').read()).split('\n')
        texts.append((p,cache[p]))
    for name in names:
        total_pairs+=1
        pat=re.compile(r'(?<![\w.>])'+re.escape(name)+r'\b|(?:\.|->)\s*'+re.escape(name)+r'\b')
        n_plain=0
        for p,lines in texts:
            for i,l in enumerate(lines):
                if not re.search(r'\b'+re.escape(name)+r'\b', l): continue
                s=l.strip()
                # remove atomic call spans (rough: drop text from ATOMIC_ to end of its first argument)
                s2=re.sub(r'\bATOMIC_[A-Z_0-9]+\s*\(\s*&?\s*(?:[\w\.\->\[\]\(\)]*?)'+re.escape(name)+r'\b','ATOM',s)
                if not re.search(r'\b'+re.escape(name)+r'\b', s2): continue
                if re.search(r'\bK_?\(\s*'+re.escape(name.rstrip('_'))+r'_?\s*\)',s2) or 'TO_STRING_KV' in s2 or re.search(r'"\w*"\s*,\s*'+re.escape(name),s2):
                    logonly[(stem,name)]+=1; continue
                if re.match(r'^[\w:<>,\s\*&]+\b'+re.escape(name)+r'\s*(\[[^\]]*\])?\s*(=[^=].*)?;\s*$',s2) or re.match(r'^(volatile\s+)?[\w:<>]+\s+'+re.escape(name),s2):
                    decl[(stem,name)]+=1; continue
                if re.match(r'^[:,]\s*'+re.escape(name)+r'\s*\(',s2) or re.match(r'^'+re.escape(name)+r'\s*\([^;]*\)\s*,?\s*$',s2):
                    decl[(stem,name)]+=1; continue
                n_plain+=1
                if len(examples)<15 and name in ('ref_cnt_','state_','stopped_','is_paused_'):
                    examples.append(f'{os.path.relpath(p,ROOT)}:{i+1}: {s[:120]}')
        if n_plain:
            mixed_pairs+=1; plain_lines_total+=n_plain; plain[(stem,name)]=n_plain
print('member-convention (stem,name) pairs:', total_pairs)
print('pairs with at least one plain access line in own stem files:', mixed_pairs)
print('plain access lines in total:', plain_lines_total)
print('pairs whose only other mentions are log/print lines:', sum(1 for k in logonly if k not in plain))
print('examples:'); print('\n'.join(examples))
top=plain.most_common(15)
for (stem,name),c in top: print(f'{c:4d} {stem} {name}')
