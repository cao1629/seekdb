# Heuristic: count non-empty `case` bodies that fall through into the next label (no break/return/
# continue/goto/throw/abort before the next case/default at the same switch depth).
# usage: python3 -I fallthrough.py
import os
import re

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
LABEL = re.compile(r'\b(?:case\s+[^:;{}]+|default)\s*:(?!:)')
TERM = re.compile(r'\b(?:break|return|continue|goto|throw|ob_abort|abort|exit|OB_ASSERT\s*\(\s*false)\b')
total_cases = 0
fall = 0
examples = []
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        p = os.path.join(dp, f)
        t = open(p, encoding='utf-8').read()
        for sm in re.finditer(r'\bswitch\s*\(', t):
            # find body
            i = t.find('{', sm.end())
            if i < 0:
                continue
            d = 0
            j = i
            while j < len(t):
                if t[j] == '{':
                    d += 1
                elif t[j] == '}':
                    d -= 1
                    if d == 0:
                        break
                j += 1
            body = t[i + 1:j]
            # keep only depth-0 text of the switch body (nested switches handled by their own pass)
            flat = []
            d = 0
            for ch in body:
                if ch == '{':
                    d += 1
                    flat.append(' ')
                    continue
                if ch == '}':
                    d -= 1
                    flat.append(' ')
                    continue
                flat.append(ch if d == 0 else (ch if ch in ';' else ' '))
            flat = ''.join(flat)
            # but terminators inside nested blocks still terminate if they are the last statement;
            # approximate by searching the full (non-flattened) segment for a terminator
            labels = list(LABEL.finditer(flat))
            for a, b in zip(labels, labels[1:]):
                total_cases += 1
                seg_full = body[a.end():b.start()]
                seg_flat = flat[a.end():b.start()]
                if seg_full.strip() == '':
                    continue  # empty label: grouped cases, maps to `A | B =>`
                tail = seg_full.rstrip()
                if TERM.search(seg_full):
                    continue
                fall += 1
                if len(examples) < 8:
                    examples.append(f'{os.path.relpath(p, ROOT)}:{t.count(chr(10), 0, i + 1 + a.start()) + 1}')
            total_cases += 1 if labels else 0
print('case/default labels examined:', total_cases)
print('non-empty case bodies with no break/return/... before the next label (fall-through, estimate):', fall)
for e in examples:
    print('  e.g.', e)
