# Classify container template instantiations by their allocator argument.
# Usage: python3 -I containers.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
NAMES = ['ObSEArray', 'ObArray', 'ObFixedArray', 'ObSArray', 'Ob2DArray', 'ObList', 'ObHashMap', 'ObHashSet', 'ObLinearHashMap']
HEAD = re.compile(r'\b(?:hash::|common::)?(' + '|'.join(NAMES) + r')\s*<')


def targs(s, i):
    # s[i] == '<' ; return list of top-level template args and end index
    depth, cur, args = 0, [], []
    j = i
    while j < len(s):
        c = s[j]
        if c == '<':
            depth += 1
            if depth > 1:
                cur.append(c)
        elif c == '>':
            depth -= 1
            if depth == 0:
                args.append(''.join(cur).strip())
                return args, j
            cur.append(c)
        elif c == ',' and depth == 1:
            args.append(''.join(cur).strip())
            cur = []
        elif c in ';{}' or (c == ')' and depth == 1 and '(' not in ''.join(cur)):
            return None, j
        else:
            cur.append(c)
        j += 1
    return None, j


stats = collections.defaultdict(collections.Counter)
total = collections.Counter()
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        s = open(p, encoding='utf-8', errors='replace').read()
        for m in HEAD.finditer(s):
            name = m.group(1)
            args, end = targs(s, m.end() - 1)
            if args is None:
                continue
            total[name] += 1
            if name in ('ObSEArray',):
                alloc = args[2] if len(args) >= 3 else '<default ModulePageAllocator>'
            elif name in ('ObArray', 'ObSArray', 'ObFixedArray'):
                alloc = args[1] if len(args) >= 2 else '<default>'
            elif name == 'Ob2DArray':
                alloc = args[2] if len(args) >= 3 else '<default>'
            elif name == 'ObList':
                alloc = args[1] if len(args) >= 2 else '<default>'
            elif name in ('ObHashMap',):
                alloc = 'args=%d' % len(args)
            elif name in ('ObHashSet',):
                alloc = 'args=%d' % len(args)
            else:
                alloc = 'args=%d' % len(args)
            alloc = re.sub(r'\s+', ' ', alloc).replace('common::', '').replace('lib::', '')
            stats[name][alloc] += 1
for name in NAMES:
    print(name, 'parsed', total[name])
    for a, n in stats[name].most_common(8):
        print('   %6d  %s' % (n, a[:90]))
