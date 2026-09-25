#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import csv, os, re, sys, collections
ROOT = '/Users/colin/seekdb-dev/migrate-to-rust'
SRC = ROOT + '/src'
tsv = sys.argv[1]
show = len(sys.argv) > 2 and sys.argv[2] == 'show'
onlyrows = set(int(x) for x in sys.argv[3].split(',')) if len(sys.argv) > 3 else None
index = collections.defaultdict(list)
for base in (SRC, ROOT + '/migration'):
    for dp, dn, fn in os.walk(base):
        if '/.git' in dp or '/evidence/' in dp + '/' : continue
        for f in fn:
            index[f].append(os.path.join(dp, f))
sdk = '/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk/usr/include/c++/v1/__algorithm'
for f in os.listdir(sdk):
    index[f].append(os.path.join(sdk, f))
lines = {}
def getlines(p):
    if p not in lines:
        with open(p, 'r', errors='replace') as fh:
            lines[p] = fh.read().split('\n')
    return lines[p]
def resolve(tok):
    base = os.path.basename(tok)
    cands = index.get(base, [])
    if '/' in tok:
        t = tok[4:] if tok.startswith('src/') else tok
        c2 = [c for c in cands if c.endswith('/' + t)]
        if c2: return c2
    return cands
TOK_RE = re.compile(r'(\()|(\))|([;.]\s)|([A-Za-z0-9_./-]+\.(?:cpp|hpp|h|ipp|c|y|l|inc|def|tsv|md))(?::(\d+)(?:-(\d+))?)?\b|(?<![A-Za-z0-9_]):(\d+)(?:-(\d+))?')
with open(tsv) as fh:
    rows = list(csv.reader(fh, delimiter='\t', quoting=csv.QUOTE_NONE))
for i, r in enumerate(rows[1:], 1):
    if onlyrows and i not in onlyrows: continue
    rowfile = r[0]
    for col in (4, 5):
        text = r[col]
        base_cur = rowfile; base_paths = resolve(rowfile)
        cur, curpaths = base_cur, base_paths
        tsv_active = False
        stack = []
        prev_src = (base_cur, base_paths)
        for m in TOK_RE.finditer(text):
            if m.group(1):
                stack.append((cur, curpaths, tsv_active)); continue
            if m.group(2):
                if stack: cur, curpaths, tsv_active = stack.pop()
                continue
            if m.group(3):
                if tsv_active:
                    cur, curpaths = (stack[-1][0], stack[-1][1]) if stack else (base_cur, base_paths)
                    if not stack: cur, curpaths = prev_src
                    tsv_active = False
                continue
            if m.group(4):
                tok = m.group(4)
                paths = resolve(tok)
                if not paths:
                    print(f'row {i} c{col}: MISSING {tok}'); continue
                if len(paths) > 1 and not tok.endswith('.tsv'):
                    sizes = sorted(((len(getlines(p)), p.replace(ROOT+'/','')) for p in paths), reverse=True)
                    print(f'row {i} c{col}: AMBIGUOUS {tok} -> {sizes}')
                    paths = [os.path.join(ROOT, sizes[0][1])]
                if tok.endswith('.tsv'):
                    if not tsv_active: prev_src = (cur, curpaths)
                    tsv_active = True
                cur, curpaths = tok, paths
                if m.group(5) is None:
                    continue
                a = int(m.group(5)); b = int(m.group(6)) if m.group(6) else a
            else:
                a = int(m.group(7)); b = int(m.group(8)) if m.group(8) else a
            if not curpaths: continue
            p = curpaths[0]; L = getlines(p); n = len(L)
            if b > n or a > b:
                print(f'row {i} c{col}: OUT OF RANGE {cur}:{a}-{b} (len {n})')
            elif show:
                print(f'row {i} c{col}: {cur}:{a} | {L[a-1].strip()[:120]}')
        prev_src = (base_cur, base_paths)
