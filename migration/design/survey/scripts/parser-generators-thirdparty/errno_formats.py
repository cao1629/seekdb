#!/usr/bin/env python3
"""Inventory printf conversions in ob_errno.def user-message formats.
Usage: python3 -I errno_formats.py REPO"""
import re, sys, os, collections
repo = sys.argv[1]
txt = open(os.path.join(repo, 'src/share/ob_errno.def'), encoding='utf-8', errors='replace').read()
defs = re.findall(r'^(DEFINE_[A-Z_]+)\((.*)\);\s*$', txt, re.M)
spec = re.compile(r'%[-+ #0]*(\*|\d+)?(\.(\*|\d+))?(hh|h|ll|l|L|z|j|t)?[diouxXeEfgGcsp%]')
kinds = collections.Counter(); per_msg = collections.Counter(); mysql_mapped = 0; total = 0
for kind, body in defs:
    strs = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    total += 1
    fmts = strs[1:] if kind != 'DEFINE_OTHER_MSG_FMT' else strs  # first quoted string is SQLSTATE
    n = 0
    for s in fmts:
        for m in spec.finditer(s):
            kinds[m.group(0)] += 1; n += 1
    per_msg[n] += 1
    parts = [p.strip() for p in body.split(',')]
    if len(parts) > 2 and parts[2] not in ('-1',):
        mysql_mapped += 1
print('definitions:', total, ' with a MySQL errno (3rd field != -1):', mysql_mapped)
print('conversion specs (all formats):')
for k, v in kinds.most_common():
    print(f'  {k:8s} {v}')
print('specs per definition:', dict(sorted(per_msg.items())))
