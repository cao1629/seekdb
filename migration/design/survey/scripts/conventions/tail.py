# For "straight propagation" candidate functions (retflow.py markers absent, returns ret), check whether
# any top-level statement AFTER the first fallible construct runs unguarded (would be skipped by `?`).
# usage: python3 -I tail.py [sample_n] [seed]
import os
import random
import re
import sys

sys.path.insert(0, '/tmp/conv-survey-7d907abfa-38738/scripts')
import retflow  # noqa: E402  (reuses DECL/M/find_body)

ROOT = retflow.ROOT
FALLIBLE = re.compile(r'\bOB_FAIL\s*\(|\bOB_SUCC\s*\(|\bret\s*=(?!=)(?!\s*(?:common::)?OB_SUCCESS\b)|\bFAILEDx\s*\(|\b(?:CK|OX|OZ|OV)\s*\(')
GUARD = re.compile(r'^\s*(?:(?:if|for|while)\s*\(\s*(?:OB_SUCC\s*\(\s*ret\s*\)|OB_SUCCESS\s*==\s*ret|ret\s*==\s*OB_SUCCESS|OB_LIKELY\s*\(\s*OB_SUCCESS\s*==\s*ret|OB_FAIL\s*\(\s*ret\s*\)\s*\)\s*\{\s*\}\s*else|OB_SUCCESS\s*!=\s*ret\s*\)\s*\{\s*\}\s*else)|(?:CK|OX|OZ|OV|OZX1|OZX2)\s*\()')
GUARD_FOR = re.compile(r'^\s*for\s*\([^;]*;\s*(?:OB_SUCC\s*\(\s*ret\s*\)|OB_SUCCESS\s*==\s*ret)')
DECL_ONLY = re.compile(r'^(?:const\s+)?(?:[\w:]+(?:<[^;{}]*>)?\s*[*&]?\s+)+[*&]?\s*\w+\s*(?:\[[^\]]*\])?\s*(?:=\s*(?:NULL|nullptr|0|false|true|-?\d+|OB_\w+|\w+)\s*)?;?$|^(?:const\s+)?[\w:]+(?:<[^;{}]*>)?\s+\w+\s*;?$')
CHAIN = re.compile(r'^\s*(?:else\b|if\s*\(\s*OB_FAIL\s*\(|if\s*\(\s*OB_UNLIKELY\s*\(|if\s*\(\s*OB_ISNULL|if\s*\()')


def top_level(body):
    """Split a function body (including braces) into top-level statements/blocks."""
    s = body[1:-1]
    out = []
    cur = ''
    d = 0
    p = 0
    i = 0
    while i < len(s):
        ch = s[i]
        cur += ch
        if ch == '(':
            p += 1
        elif ch == ')':
            p -= 1
        elif ch == '{':
            d += 1
        elif ch == '}':
            d -= 1
            if d == 0 and p == 0:
                # a block ended; if followed by else, keep accumulating
                rest = s[i + 1:].lstrip()
                if not rest.startswith('else') and not rest.startswith('while') and not rest.startswith(';'):
                    out.append(cur.strip())
                    cur = ''
        elif ch == ';' and d == 0 and p == 0:
            out.append(cur.strip())
            cur = ''
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def classify(body):
    stmts = top_level(body)
    first = None
    for k, st in enumerate(stmts):
        if FALLIBLE.search(st):
            first = k
            break
    if first is None:
        return 'no-fallible', []
    unguarded = []
    kind = 'clean'
    for st in stmts[first + 1:]:
        if re.fullmatch(r'return\s+ret\s*;?', st):
            continue
        if GUARD.search(st) or GUARD_FOR.search(st):
            continue
        unguarded.append(st)
        if FALLIBLE.search(st.split('{')[0]) or re.match(r'(?:if|while)\s*\(\s*OB_FAIL\s*\(', st):
            kind = 'unguarded-fallible'
        elif kind == 'clean' and not DECL_ONLY.match(st):
            kind = 'unguarded-effect'
    if kind == 'clean' and unguarded:
        kind = 'decl-only-tail'
    return kind, unguarded


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    counts = {'clean': 0, 'decl-only-tail': 0, 'unguarded-effect': 0, 'unguarded-fallible': 0, 'no-fallible': 0}
    tails = []
    total = 0
    for dp, dn, fn in os.walk(ROOT):
        for f in fn:
            path = os.path.join(dp, f)
            t = open(path, encoding='utf-8').read()
            seen = set()
            for m in retflow.DECL.finditer(t):
                b = retflow.find_body(t, m.start())
                if not b or b[0] in seen:
                    continue
                seen.add(b[0])
                body = t[b[0]:b[1]]
                flags = [k for k, rx in retflow.M.items() if rx.search(body)]
                if flags or not re.search(r'\breturn\s+ret\s*;', body):
                    continue
                total += 1
                c, ug = classify(body)
                counts[c] += 1
                if c in ('unguarded-fallible', 'unguarded-effect'):
                    tails.append((c, os.path.relpath(path, ROOT), t.count('\n', 0, b[0]) + 1, ug[-1][:90].replace('\n', ' ')))
    print('straight-propagation candidates:', total)
    for k, v in counts.items():
        print(f'  {k}: {v} ({100.0 * v / total:.1f}%)')
    if sample_n:
        random.seed(seed)
        for x in random.sample(tails, min(sample_n, len(tails))):
            print('TAIL', x[0], f'{x[1]}:{x[2]}', '|', x[3])


main()
