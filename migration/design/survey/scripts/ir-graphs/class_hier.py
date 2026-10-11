#!/usr/bin/env python3
"""Transitive subclass counts for selected roots, from `class X : public Y` heads under src/.

Usage: python3 -I class_hier.py <repo_root> Root1 [Root2 ...]
Heuristic: parses `class|struct Name [final] : [virtual] public|protected|private Base[<...>] [, ...]`
heads (possibly spanning 3 lines). Template bases are reduced to their name.
"""
import os, re, sys, collections
root = sys.argv[1]
roots = sys.argv[2:]
head = re.compile(r'\b(class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\s*(?:final\s*)?:\s*((?:(?:public|protected|private|virtual)\s+)*[\w:<>,\s]+?)\s*\{', re.S)
parents = collections.defaultdict(set)
where = {}
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.h', '.cpp', '.ipp')):
            continue
        p = os.path.join(dp, f)
        t = open(p, encoding='utf-8', errors='replace').read()
        for m in head.finditer(t):
            name = m.group(2)
            bases = m.group(3)
            # strip template args
            depth = 0; cur = ''; parts = []
            for ch in bases:
                if ch == '<': depth += 1
                elif ch == '>': depth -= 1
                elif ch == ',' and depth == 0:
                    parts.append(cur); cur = ''; continue
                if depth == 0 and ch not in '<>':
                    cur += ch
            parts.append(cur)
            for b in parts:
                b = re.sub(r'\b(public|protected|private|virtual)\b', '', b).strip()
                b = b.split('::')[-1].strip()
                if b:
                    parents[name].add(b)
            line = t.count('\n', 0, m.start()) + 1
            where.setdefault(name, f'{os.path.relpath(p, root)}:{line}')
children = collections.defaultdict(set)
for c, ps in parents.items():
    for p in ps:
        children[p].add(c)
for r in roots:
    seen = set(); stack = [r]
    while stack:
        x = stack.pop()
        for c in children.get(x, ()):
            if c not in seen:
                seen.add(c); stack.append(c)
    direct = children.get(r, set())
    print(f'{r}: direct={len(direct)} transitive={len(seen)}  (root at {where.get(r, "?")})')
    if '-v' in os.environ.get('CH_FLAGS', ''):
        for c in sorted(seen):
            print('   ', c, where.get(c))
