#!/usr/bin/env python3
"""Live hand-written files of 4,000 lines or more, split by core scope (core_scope.py's rows)."""
import json, sys
sys.path.insert(0, '/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/01-crates')
from incgraph import category
ns = {}
exec(open('core_scope.py').read().split("seen = set()")[0], ns)
L = ns['lines']; live = ns['live']; ROWS = ns['ROWS']; m = ns['m']
core = set()
for name, pats in ROWS:
    core |= {f for f in live if f.startswith('src/') and m(f[4:], pats)}
big = [(L[f], f) for f in live if category(f) == 'hand' and L[f] >= 4000]
inc = [(n, f) for n, f in big if f in core]
print('files', len(big), 'lines', sum(n for n, _ in big))
print('in core scope', len(inc), sum(n for n, _ in inc))
print('outside core scope', len(big) - len(inc), sum(n for n, _ in big) - sum(n for n, _ in inc))
print('headers', sorted((n, f) for n, f in big if not f.endswith(('.cpp', '.cc', '.c'))))
