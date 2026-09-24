#!/usr/bin/env python3
"""Check that the crate graph of ARCHITECTURE.md 1.1 is acyclic.

usage: check_crate_graph.py ARCHITECTURE.md [allowed.tsv]

Reads the table of ARCHITECTURE.md section 1.1 (rows "| N | crate | ... | May also use |"),
adds crates 1 and 2 to every row from 7 on except sql-nio (the table's own rule), and checks:
  1. the numbers run 1..N with no gap and every crate name is unique;
  2. every crate a row names exists and comes earlier in the table;
  3. a topological sort (Kahn) reaches every crate, so the graph has no cycle;
  4. optionally, the rows equal allowed.tsv (crate<TAB>space-separated crate names), the rows the
     crate-edge scripts were run with.
Prints the result and exits 1 on any failure.
"""
import re
import sys

text = open(sys.argv[1]).read()
sec = text.split('### 1.1 The crates', 1)[1].split('### 1.2', 1)[0]
rows = []
for line in sec.splitlines():
    m = re.match(r'^\|\s*(\d+)\s*\|\s*([a-z0-9-]+)\b[^|]*\|(.*)\|\s*$', line)
    if m:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows.append((int(cells[0]), re.sub(r'\s*†\s*$', '', cells[1]).strip(), cells[-1]))

errors = []
nums = [n for n, _, _ in rows]
if nums != list(range(1, len(rows) + 1)):
    errors.append(f'crate numbers are not 1..{len(rows)} in order: {nums}')
names = [c for _, c, _ in rows]
if len(set(names)) != len(names):
    errors.append('duplicate crate names')
num_of = {c: n for n, c, _ in rows}
name_of = {n: c for n, c, _ in rows}


def parse_uses(cell):
    cell = cell.split(',', 1)[0] if cell.startswith('3 only') else cell
    cell = re.sub(r'only.*$', '', cell)
    if cell.strip() in ('-', ''):
        return set()
    out = set()
    for part in cell.split(','):
        part = part.strip()
        if not part:
            continue
        if part.startswith('none'):
            continue
        m = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part)
        if m:
            out |= set(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            errors.append(f'cannot read "{part}"')
    return out


deps = {}
for n, c, cell in rows:
    d = parse_uses(cell)
    if n >= 7 and c != 'sql-nio':
        d |= {1, 2}
    deps[n] = d
    for x in sorted(d):
        if x not in name_of:
            errors.append(f'{n} {c} names unknown crate {x}')
        elif x >= n:
            errors.append(f'{n} {c} names {x} {name_of[x]}, which is not earlier in the table')

indeg = {n: 0 for n in deps}
users = {n: [] for n in deps}
for n, d in deps.items():
    for x in d:
        if x in indeg:
            indeg[n] += 1
            users[x].append(n)
ready = sorted(n for n, k in indeg.items() if k == 0)
order = []
while ready:
    n = ready.pop(0)
    order.append(n)
    for u in users[n]:
        indeg[u] -= 1
        if indeg[u] == 0:
            ready.append(u)
    ready.sort()
if len(order) != len(deps):
    errors.append('cycle among: ' + ', '.join(name_of[n] for n in deps if n not in order))

if len(sys.argv) > 2:
    allowed = {}
    for line in open(sys.argv[2]):
        a, _, rest = line.rstrip('\n').partition('\t')
        allowed[a] = set(rest.split())
    for n, c, _ in rows:
        mine = {name_of[x] for x in deps[n]}
        theirs = set(allowed.get(c, set()))
        if n >= 7 and c != 'sql-nio':
            theirs |= {'ob-errno', 'ob-base'}
        if mine != theirs:
            errors.append(f'{c}: table has {sorted(mine - theirs)} extra, {sorted(theirs - mine)} missing against {sys.argv[2]}')
    extra = set(allowed) - set(names)
    if extra:
        errors.append(f'{sys.argv[2]} names crates the table lacks: {sorted(extra)}')

edges = sum(len(d) for d in deps.values())
if errors:
    print('NOT ACYCLIC OR INCONSISTENT')
    for e in errors:
        print('  ' + e)
    sys.exit(1)
print(f'acyclic: {len(rows)} crates, {edges} allowed edges, every edge points to an earlier crate; '
      f'topological order found for all {len(order)} crates' +
      (f'; rows equal {sys.argv[2]}' if len(sys.argv) > 2 else ''))
