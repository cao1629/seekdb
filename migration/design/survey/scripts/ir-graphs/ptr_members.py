#!/usr/bin/env python3
"""Count pointer-typed data members (and containers of pointers) in given headers, grouped by
pointee type. A member = a line ending in `name_;` (OceanBase convention: members end with `_`).
Usage: python3 -I ptr_members.py <repo_root> file1.h [file2.h ...]
"""
import os, re, sys, collections
root = sys.argv[1]
raw_ptr = re.compile(r'^\s*(?:const\s+)?(?:[\w:]+::)?(\w+)\s*(?:const\s*)?\*\s*(?:const\s*)?(\w+_)\s*(?:=\s*[^;]*)?;')
dbl_ptr = re.compile(r'^\s*(?:const\s+)?(?:[\w:]+::)?(\w+)\s*\*\s*\*\s*(\w+_)\s*;')
cont_ptr = re.compile(r'^\s*(?:common::)?(\w*(?:Array|Vector|List|Set|Map)\w*)\s*<\s*(?:const\s+)?(?:[\w:]+::)?(\w+)\s*\*[^;]*>\s*(\w+_)\s*;')
for f in sys.argv[2:]:
    by = collections.Counter(); conts = collections.Counter(); n = 0; nc = 0; nd = 0
    for line in open(os.path.join(root, f), encoding='utf-8', errors='replace'):
        if line.lstrip().startswith(('//', '*', 'return', 'typedef')):
            continue
        m = dbl_ptr.match(line)
        if m:
            nd += 1; by[m.group(1) + '**'] += 1; continue
        m = raw_ptr.match(line)
        if m:
            n += 1; by[m.group(1)] += 1; continue
        m = cont_ptr.match(line)
        if m:
            nc += 1; conts[m.group(2)] += 1
    print(f'{f}: raw pointer members={n} double-pointer members={nd} pointer-containers={nc}')
    print('   pointees:', ', '.join(f'{k}={v}' for k, v in by.most_common(12)))
    print('   container elems:', ', '.join(f'{k}={v}' for k, v in conts.most_common(12)))
