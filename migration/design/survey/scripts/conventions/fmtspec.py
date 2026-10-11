# Census of printf conversion specifiers used in format strings passed to databuff_printf / BUF_PRINTF /
# snprintf / LOG_USER_* family / _LOG_* printf-style logs (string-kept stripped cache).
# usage: python3 -I fmtspec.py
import collections
import os
import re

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped_keepstr'
CALL = re.compile(r'\b(databuff_printf|BUF_PRINTF|snprintf|vsnprintf|sprintf|fprintf|printf|_OB_LOG|_LOG_\w+|_\w+_LOG)\s*\(')
STR = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
SPEC = re.compile(r'%[-+ #0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|z|j|t|L)?[diouxXeEfFgGcspn%]|%"\s*P\w+')
per_call = collections.Counter()
spec = collections.Counter()
spec_by_family = collections.defaultdict(collections.Counter)
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        t = open(os.path.join(dp, f), encoding='utf-8').read()
        for m in CALL.finditer(t):
            fam = m.group(1)
            fam = '_LOG*' if fam.startswith('_') else fam
            # look at the next ~400 chars for the first string literal run (format string)
            seg = t[m.end():m.end() + 600]
            # format string = consecutive adjacent string literals near the beginning
            lits = []
            pos = 0
            for sm in STR.finditer(seg):
                if lits and seg[pos:sm.start()].strip() not in ('',) and not re.fullmatch(r'\s*(?:PRI\w+|"\s*)\s*', seg[pos:sm.start()]):
                    break
                lits.append(sm.group(1))
                pos = sm.end()
                if len(lits) > 8:
                    break
            if not lits:
                continue
            per_call[fam] += 1
            fmt = ''.join(lits)
            for s in SPEC.findall(fmt):
                if s == '%%':
                    continue
                spec[s] += 1
                spec_by_family[fam][s] += 1
print('calls with a literal format string:', dict(per_call))
print('top specifiers overall:', spec.most_common(40))
floaty = {k: v for k, v in spec.items() if k[-1] in 'eEfFgG'}
print('float specifiers:', sorted(floaty.items(), key=lambda x: -x[1]))
print('%p:', sum(v for k, v in spec.items() if k.endswith('p')))
