#!/usr/bin/env python3
"""Estimate how many get_param_expr() call arguments flow into an `ObRawExpr *&` parameter.

1. Collect function names declared anywhere under src/ whose parameter list contains a
   non-const `Ob...RawExpr *&` (slot) parameter.
2. For each line where get_param_expr( appears as a call argument, take the innermost
   enclosing callee name and check membership in that set (overloads => over-estimate).
3. Also count total `ObRawExpr *&`-style slot parameters and `ObRawExpr **` occurrences.
Usage: python3 -I ref_slot_funcs.py <repo_root>
"""
import os, re, sys, collections
root = sys.argv[1]
src = os.path.join(root, 'src')
files = []
for dp, dn, fn in os.walk(src):
    for f in fn:
        if f.endswith(('.h', '.cpp', '.ipp')):
            files.append(os.path.join(dp, f))

slot_param = re.compile(r'\b(?<!const )(Ob\w*RawExpr)\s*\*\s*&\s*\w+')
# function declaration heads: name( ... ObRawExpr *&x ... )
func_head = re.compile(r'\b(\w+)\s*\(([^;{}()]|\([^()]*\))*?\b(?<!const )Ob\w*RawExpr\s*\*\s*&\s*\w+', re.S)
names = collections.Counter()
slot_params_total = 0
slot_params_by_dir = collections.Counter()
dbl_ptr = 0
texts = {}
for p in files:
    with open(p, encoding='utf-8', errors='replace') as fh:
        t = fh.read()
    texts[p] = t
    rel = os.path.relpath(p, root)
    d = '/'.join(rel.split('/')[1:3])
    for m in slot_param.finditer(t):
        # skip `const ObRawExpr *&` (pointer-to-const out param is still a slot, keep separately)
        slot_params_total += 1
        slot_params_by_dir[d] += 1
    dbl_ptr += len(re.findall(r'\bOb\w*RawExpr\s*\*\s*\*', t))
    for m in func_head.finditer(t):
        nm = m.group(1)
        if nm in ('if', 'for', 'while', 'switch', 'return', 'OB_FAIL', 'OB_SUCC', 'SMART_CALL', 'sizeof'):
            continue
        names[nm] += 1

print('distinct function names with a non-const Ob*RawExpr *& parameter:', len(names))
print('non-const Ob*RawExpr *& tokens (params+locals+returns):', slot_params_total)
for d, c in slot_params_by_dir.most_common(8):
    print(f'   {d:28s} {c}')
print('Ob*RawExpr ** occurrences:', dbl_ptr)

call_arg = re.compile(r'(\w+)\s*\(([^()]|\([^()]*\))*?\bget_param_expr\s*\(')
hit = collections.Counter()
total_arg = 0
for p, t in texts.items():
    for line in t.splitlines():
        if 'get_param_expr' not in line:
            continue
        for m in call_arg.finditer(line):
            callee = m.group(1)
            if callee in ('get_param_expr', 'OB_FAIL', 'OB_SUCC', 'SMART_CALL', 'OB_ISNULL', 'OB_NOT_NULL',
                          'if', 'CK', 'OZ', 'K', 'static_cast', 'const_cast', 'OB_UNLIKELY', 'OB_LIKELY', 'while', 'for'):
                continue
            total_arg += 1
            if callee in names:
                hit[callee] += 1
print('call-argument uses of get_param_expr with a named callee:', total_arg)
print('  ... whose callee name has an Ob*RawExpr *& overload (upper bound of slot writes):', sum(hit.values()))
for k, v in hit.most_common(15):
    print(f'   {k:45s} {v}')
