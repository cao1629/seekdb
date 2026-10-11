#!/usr/bin/env python3
"""Classify get_param_expr( occurrences under src/ at the migration base.

Usage: python3 -I param_expr_sites.py <repo_root>
Categories (heuristic, line based):
  decl     : declaration/definition of get_param_expr itself
  bind_ref : bound to a reference slot  `ObRawExpr *&x = ...get_param_expr(...)`
  assign   : direct assignment           `...get_param_expr(i) = y`
  pass_ref : passed straight to a function whose parameter list contains `ObRawExpr *&`
             at that position is not resolvable by regex; we report "arg" (any call
             argument) separately and estimate writes by matching known *& callees.
  read     : everything else
"""
import os, re, sys, collections

root = sys.argv[1]
src = os.path.join(root, 'src')
pat = re.compile(r'\bget_param_expr\s*\(')
decl_re = re.compile(r'(virtual\s+)?(const\s+)?(ObRawExpr|ObConstRawExpr|ObRawExpr\s*\*\s*&|\w+)\s*\*?\s*&?\s*(\w+::)?get_param_expr\s*\(\s*(const\s+)?int64_t')
bind_re = re.compile(r'\*\s*&\s*\w+\s*=\s*[^;]*\bget_param_expr\s*\(')
assign_re = re.compile(r'\bget_param_expr\s*\([^()]*(\([^()]*\))?[^()]*\)\s*=(?!=)')
arg_re = re.compile(r'\(\s*[\w>\-\.\*\(\)]*get_param_expr\s*\(|,\s*[\w>\-\.\*\(\)]*get_param_expr\s*\(')

counts = collections.Counter()
by_dir = collections.defaultdict(collections.Counter)
examples = collections.defaultdict(list)
for dp, dn, fn in os.walk(src):
    for f in fn:
        if not f.endswith(('.h', '.cpp', '.ipp')):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        d = '/'.join(rel.split('/')[1:3])
        with open(p, encoding='utf-8', errors='replace') as fh:
            for ln, line in enumerate(fh, 1):
                n = len(pat.findall(line))
                if not n:
                    continue
                if decl_re.search(line):
                    cat = 'decl'
                elif bind_re.search(line):
                    cat = 'bind_ref'
                elif assign_re.search(line):
                    cat = 'assign'
                elif arg_re.search(line):
                    cat = 'arg'
                else:
                    cat = 'read'
                counts[cat] += n
                by_dir[d][cat] += n
                if len(examples[cat]) < 6:
                    examples[cat].append(f'{rel}:{ln}: {line.strip()[:140]}')
print('total occurrences', sum(counts.values()))
for k, v in counts.most_common():
    print(f'{k:9s} {v}')
print('\nby dir (top 10 by total)')
for d, c in sorted(by_dir.items(), key=lambda kv: -sum(kv[1].values()))[:10]:
    print(f'{d:30s} total={sum(c.values()):5d} ' + ' '.join(f'{k}={v}' for k, v in sorted(c.items())))
print('\nexamples')
for k, v in examples.items():
    print('==', k)
    for e in v:
        print('  ', e)
