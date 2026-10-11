# Classify functions that declare `int ret = OB_SUCCESS` (or INIT_SUCC(ret)) by how they consume `ret`
# after a failure.  Uses the cached comment/string-stripped node files.
# usage: python3 -I retflow.py [sample_n] [seed]
import collections
import os
import random
import re
import sys

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
DECL = re.compile(r'\bint\s+ret\s*=\s*(?:::)?(?:oceanbase::)?(?:common::)?OB_SUCCESS\b|\bINIT_SUCC\s*\(\s*ret\s*\)')
KW = {'if', 'for', 'while', 'switch', 'catch', 'return', 'sizeof', 'defined', 'decltype', 'alignof', 'typeof', '__typeof__'}
QUAL = re.compile(r'(?:\s|const|override|final|noexcept(?:\([^)]*\))?|volatile|&|->\s*[\w:<>,\s*&]+|__attribute__\s*\(\(.*?\)\))*$')

M = collections.OrderedDict([
    ('reset ret = OB_SUCCESS', re.compile(r'(?<!int )\bret\s*=\s*(?:common::)?OB_SUCCESS\s*;')),
    ('dispatch ret ==/!= OB_X', re.compile(r'\b(?:common::)?OB_(?!SUCCESS\b)[A-Z][A-Z0-9_]*\s*[!=]=\s*ret\b|\bret\s*[!=]=\s*(?:common::)?OB_(?!SUCCESS\b)[A-Z][A-Z0-9_]*\b')),
    ('tmp_ret', re.compile(r'\btmp_ret\b')),
    ('if(OB_FAIL(ret)) / if(OB_SUCCESS != ret) block', re.compile(r'\bif\s*\(\s*(?:OB_UNLIKELY\s*\(\s*)?(?:OB_FAIL\s*\(\s*ret\s*\)|(?:common::)?OB_SUCCESS\s*!=\s*ret\b|ret\s*!=\s*(?:common::)?OB_SUCCESS\b)\s*\)?\s*\)\s*\{(?!\s*\})')),
    ('COVER_SUCC', re.compile(r'\bCOVER_SUCC\s*\(')),
    ('ret = <call>(...) outside OB_FAIL', re.compile(r'(?<![\w(])ret\s*=\s*(?!OB_[A-Z_0-9]+\s*;)(?!(?:common::)?OB_SUCCESS)[\w:.\->]+\s*\(')),
])
EARLY_RETURN = re.compile(r'\breturn\s+(?!ret\s*;)[^;]+;')


def find_body(t, p):
    """Return (start, end) of the function body enclosing position p, or None."""
    depth = 0
    i = p
    while i > 0:
        i -= 1
        c = t[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth > 0:
                depth -= 1
                continue
            # candidate block start at i: check header
            j = i - 1
            head = t[max(0, i - 400):i]
            m = QUAL.search(head)
            h = head[:m.start()] if m else head
            h = h.rstrip()
            if h.endswith(')'):
                # find matching '(' backwards
                k = len(h) - 1
                d = 0
                while k >= 0:
                    if h[k] == ')':
                        d += 1
                    elif h[k] == '(':
                        d -= 1
                        if d == 0:
                            break
                    k -= 1
                before = h[:k].rstrip()
                mm = re.search(r'([~\w]+)\s*$', before)
                name = mm.group(1) if mm else ''
                if name and name not in KW and not before.endswith(('=', ',', '(', 'return')):
                    # body start found; find end
                    d = 0
                    e = i
                    while e < len(t):
                        if t[e] == '{':
                            d += 1
                        elif t[e] == '}':
                            d -= 1
                            if d == 0:
                                return (i, e + 1, name)
                        e += 1
                    return None
            # not a function body: keep searching outward
    return None


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    funcs = []
    for dp, dn, fn in os.walk(ROOT):
        for f in fn:
            path = os.path.join(dp, f)
            t = open(path, encoding='utf-8').read()
            seen = set()
            for m in DECL.finditer(t):
                b = find_body(t, m.start())
                if not b or b[0] in seen:
                    continue
                seen.add(b[0])
                body = t[b[0]:b[1]]
                rel = os.path.relpath(path, ROOT)
                line = t.count('\n', 0, b[0]) + 1
                funcs.append((rel, line, b[2], body))
    total = len(funcs)
    stats = collections.Counter()
    clean = []
    noret = 0
    early = 0
    sizes = []
    for rel, line, name, body in funcs:
        flags = [k for k, rx in M.items() if rx.search(body)]
        for k in flags:
            stats[k] += 1
        returns_ret = bool(re.search(r'\breturn\s+ret\s*;', body))
        if not returns_ret:
            noret += 1
        if EARLY_RETURN.search(body):
            early += 1
        if not flags and returns_ret:
            clean.append((rel, line, name))
        sizes.append(body.count('\n') + 1)
    print('functions declaring ret:', total)
    for k, v in stats.items():
        print(f'  has {k}: {v} ({100.0 * v / total:.1f}%)')
    print(f'  no `return ret;` (void/bool/other return): {noret} ({100.0 * noret / total:.1f}%)')
    print(f'  has a `return <expr other than ret>;`: {early} ({100.0 * early / total:.1f}%)')
    print(f'  none of the markers and returns ret ("straight propagation" candidates): {len(clean)} ({100.0 * len(clean) / total:.1f}%)')
    sizes.sort()
    print('  body lines p50/p90/max:', sizes[len(sizes) // 2], sizes[int(len(sizes) * 0.9)], sizes[-1])
    if sample_n:
        random.seed(seed)
        for rel, line, name in random.sample(clean, sample_n):
            print('SAMPLE', f'{rel}:{line}', name)


main()
