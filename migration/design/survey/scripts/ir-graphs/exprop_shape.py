#!/usr/bin/env python3
"""Shape of the ObExprOperator hierarchy: per subclass, which virtuals it overrides and whether it
has its own data members. Usage: python3 -I exprop_shape.py <repo_root>
Heuristic class-body extraction by brace matching from `class X : ... {`.
"""
import os, re, sys, collections
root = sys.argv[1]
head = re.compile(r'\b(class|struct)\s+(\w+)\s*(?:final\s*)?:\s*([^{;]*?)\{', re.S)
classes = {}
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.h', '.cpp', '.ipp')):
            continue
        p = os.path.join(dp, f)
        t = open(p, encoding='utf-8', errors='replace').read()
        for m in head.finditer(t):
            name = m.group(2)
            bases = [re.sub(r'\b(public|protected|private|virtual)\b', '', b).strip().split('<')[0].split('::')[-1].strip()
                     for b in m.group(3).split(',')]
            # body
            i = m.end(); depth = 1
            while i < len(t) and depth:
                if t[i] == '{': depth += 1
                elif t[i] == '}': depth -= 1
                i += 1
            body = t[m.end():i-1]
            if name not in classes:
                classes[name] = (bases, body, os.path.relpath(p, root))
children = collections.defaultdict(set)
for n, (bs, _, _) in classes.items():
    for b in bs:
        children[b].add(n)
seen = set(); stack = ['ObExprOperator']
while stack:
    x = stack.pop()
    for c in children.get(x, ()):
        if c not in seen:
            seen.add(c); stack.append(c)
virts = ['calc_result_type0', 'calc_result_type1', 'calc_result_type2', 'calc_result_type3', 'calc_result_typeN',
         'cg_expr', 'assign', 'need_rt_ctx', 'calc_result0', 'calc_result1', 'calc_result2', 'calc_result3', 'calc_resultN',
         'is_valid_for_generated_column', 'set_local_session_vars', 'get_cast_mode', 'serialize', 'deserialize']
ov = collections.Counter(); with_members = 0; member_counts = []
nested_depth = collections.Counter()
for n in seen:
    bs, body, path = classes[n]
    # strip nested class bodies roughly: remove inline function bodies
    flat = re.sub(r'\{[^{}]*\}', ';', body)
    flat = re.sub(r'\{[^{}]*\}', ';', flat)
    for v in virts:
        if re.search(r'\b' + v + r'\s*\(', flat):
            ov[v] += 1
    mems = [l for l in flat.split(';') if re.search(r'\b\w+_\s*(\[\w*\])?\s*$', l.strip()) and '(' not in l]
    if mems:
        with_members += 1
        member_counts.append(len(mems))
    # depth
    d = 0; x = n
    while x in classes and x != 'ObExprOperator' and d < 10:
        x = classes[x][0][0]; d += 1
    nested_depth[d] += 1
print('ObExprOperator transitive subclasses:', len(seen))
print('subclasses with own data members:', with_members, ' (median members',
      sorted(member_counts)[len(member_counts)//2] if member_counts else 0, ')')
print('override counts:')
for v in virts:
    print(f'   {v:32s} {ov[v]}')
print('inheritance depth below ObExprOperator:', dict(sorted(nested_depth.items())))
