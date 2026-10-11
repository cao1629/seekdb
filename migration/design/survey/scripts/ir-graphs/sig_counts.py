#!/usr/bin/env python3
"""Count function definitions (in .cpp/.ipp/.h bodies) whose parameter list mentions IR types,
split into free/static vs member functions, to estimate how many signatures would need an
explicit arena/context parameter under a typed-index design.
Usage: python3 -I sig_counts.py <repo_root>
"""
import os, re, sys, collections
root = sys.argv[1]
# definition head:  ret  Class::name(params) [const] {
defn = re.compile(r'\n[ \t]*(?:static\s+|inline\s+|OB_INLINE\s+|virtual\s+)*[\w:<>\*&\s]+?\b((?:\w+::)*)(\w+)\s*\(([^;{}]*?)\)\s*(?:const\s*)?(?:override\s*)?\{', re.S)
ir = {
    'raw_expr': re.compile(r'\bOb\w*RawExpr\b(?!Factory|Copier|Utils|Visitor|Replacer|UniqueSet|Pointer|ResType)'),
    'stmt': re.compile(r'\bOb(DML|Select|Insert|Update|Delete|DelUpd|Merge)Stmt\b'),
    'log_op': re.compile(r'\bObLog\w+\b(?!Plan)'),
    'rt_expr': re.compile(r'\bObExpr\b'),
}
c = collections.Counter(); by_dir = collections.defaultdict(collections.Counter)
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.cpp', '.ipp', '.h')):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        d = '/'.join(rel.split('/')[1:3])
        t = '\n' + open(p, encoding='utf-8', errors='replace').read()
        for m in defn.finditer(t):
            name = m.group(2)
            if name in ('if', 'for', 'while', 'switch', 'return', 'sizeof', 'catch'):
                continue
            params = m.group(3)
            for k, rx in ir.items():
                if rx.search(params):
                    c[k] += 1
                    by_dir[k][d] += 1
print('function definitions whose parameter list mentions:')
for k in ir:
    top = ', '.join(f'{dd}={n}' for dd, n in by_dir[k].most_common(6))
    print(f'  {k:9s} {c[k]:6d}   [{top}]')
