#!/usr/bin/env python3
"""Count manifest units (rows of migration/manifest.tsv) per IR-affected directory.
Usage: python3 -I manifest_units.py <repo_root>
"""
import csv, os, sys, collections
root = sys.argv[1]
rows = list(csv.reader(open(os.path.join(root, 'migration/manifest.tsv')), delimiter='\t'))
print('header:', rows[0])
groups = ['src/sql/resolver', 'src/sql/rewrite', 'src/sql/optimizer', 'src/sql/code_generator',
          'src/sql/engine/expr', 'src/sql/engine', 'src/sql/das', 'src/sql/plan_cache', 'src/sql/printer',
          'src/query', 'src/pl', 'src/sql/pl']
c = collections.Counter()
for r in rows[1:]:
    s = r[0]
    for g in groups:
        if s.startswith(g + '/'):
            c[g] += 1
            break
tot = 0
for g in groups:
    print(f'{g:28s} {c[g]}')
    tot += c[g]
print('sum', tot, 'of', len(rows) - 1, 'units')
