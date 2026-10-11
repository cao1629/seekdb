# Classify the first argument of every ATOMIC_* call and estimate how many distinct fields are
# accessed atomically, and how many of those fields are ALSO accessed plainly (mixed access) in
# the same file. Heuristic; works on the stripped mirror.
# Usage: python3 -I atomic_targets.py <stripped_root>
import os, re, sys, collections

root = sys.argv[1]
call_re = re.compile(r'\b(ATOMIC_[A-Z0-9_]+)\s*\(')
SKIP = {'src/oblib/lib/atomic/ob_atomic.h'}


def first_arg(s, i):
    # i points just after '('
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return s[i:j]
            depth -= 1
        elif c == ',' and depth == 0:
            return s[i:j]
        j += 1
    return s[i:j]


kinds = collections.Counter()
fields = collections.defaultdict(set)   # field name -> set of files
field_files = collections.defaultdict(lambda: collections.defaultdict(int))
by_macro_kind = collections.Counter()
for dp, dn, fn in os.walk(root):
    for f in fn:
        full = os.path.join(dp, f)
        rel = os.path.relpath(full, root)
        if rel in SKIP:
            continue
        s = open(full, encoding='utf-8', errors='replace').read()
        for m in call_re.finditer(s):
            name = m.group(1)
            if name in ('ATOMIC_ADD_TAG', 'ATOMIC_SUB_TAG'):
                a = '&flag_'
            else:
                a = first_arg(s, m.end()).strip()
            a = re.sub(r'\s+', '', a)
            a = re.sub(r'^\((.*)\)$', r'\1', a)
            if a.startswith('&'):
                body = a[1:]
                body = re.sub(r'^\((.*)\)$', r'\1', body)
                if re.fullmatch(r'(this->)?[A-Za-z_]\w*_', body):
                    k = 'member_of_this'
                elif re.fullmatch(r'[A-Za-z_]\w*(\.|->)[A-Za-z_][\w.\->]*', body) or re.fullmatch(r'.*(\.|->)[A-Za-z_]\w*', body):
                    k = 'member_of_other'
                elif '[' in body:
                    k = 'array_elem'
                elif re.fullmatch(r'[A-Za-z_][\w:]*', body):
                    k = 'plain_var_or_global'
                else:
                    k = 'other_addr'
                fm = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$', body)
                if fm:
                    fields[fm.group(1)].add(rel)
                    field_files[fm.group(1)][rel] += 1
            elif re.fullmatch(r'[A-Za-z_]\w*', a):
                k = 'pointer_value'
            else:
                k = 'other_expr'
            kinds[k] += 1
            by_macro_kind[(name, k)] += 1

print('== ATOMIC_* first-argument kinds ==')
for k, c in kinds.most_common():
    print(f'{k:22s} {c:6d}')
print('distinct field/var names behind &x atomics:', len(fields))

# mixed access: in each file where field X is used atomically, count plain references to X
# that are not inside an ATOMIC_* argument list (rough: total refs minus atomic refs).
mixed = 0
mixed_examples = []
checked = 0
for fld, files in fields.items():
    for rel in files:
        s = open(os.path.join(root, rel), encoding='utf-8', errors='replace').read()
        total = len(re.findall(r'(?<![\w])' + re.escape(fld) + r'(?![\w])', s))
        atomic = field_files[fld][rel]
        # subtract declarations (1) and constructor init (approx 1)
        plain = total - atomic
        checked += 1
        if plain > 2:
            mixed += 1
            if len(mixed_examples) < 25:
                mixed_examples.append((rel, fld, atomic, plain))
print('(field,file) pairs checked:', checked)
print('pairs with >2 non-atomic references besides atomic ones (likely mixed access):', mixed)
for e in mixed_examples:
    print('  ', e)
