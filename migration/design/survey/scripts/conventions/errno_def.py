# Parse src/share/ob_errno.def: count entries, mysql-mapped codes, printf specifiers in M4 (user message) strings.
# usage: python3 -I errno_def.py src/share/ob_errno.def
import re, sys, collections
path = sys.argv[1]
txt = open(path, encoding='utf-8', errors='replace').read()
lines = [l for l in txt.splitlines() if not l.lstrip().startswith('//')]
body = '\n'.join(lines)
entries = [(m.group(1), re.sub(r'[\s;)]*$', '', m.group(2))) for m in re.finditer(r'^(DEFINE_[A-Z_]+)\((.*)$', body, re.M)]
kinds = collections.Counter(k for k, _ in entries)


def split_args(s):
    out = []
    cur = ''
    q = False
    esc = False
    for ch in s:
        if q:
            cur += ch
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                q = False
        else:
            if ch == '"':
                q = True
                cur += ch
            elif ch == ',':
                out.append(cur.strip())
                cur = ''
            else:
                cur += ch
    out.append(cur.strip())
    return out


mysql_mapped = 0
own_code = 0
spec = collections.Counter()
with_params = 0
total = 0
sqlstates = collections.Counter()
m3_ne_m4 = 0
codes = []
for k, a in entries:
    args = split_args(a)
    if k == 'DEFINE_OTHER_MSG_FMT':
        continue
    total += 1
    c1, c0, m1, m2, m3 = args[:5]
    m4 = args[5] if len(args) > 5 else m3
    codes.append(int(c0))
    if m1.strip() == '-1':
        own_code += 1
    else:
        mysql_mapped += 1
    sqlstates[m2] += 1
    if m4 != m3:
        m3_ne_m4 += 1
    specs = re.findall(r'%[-+ #0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|z|j|t|L)?[diouxXeEfgGcspn%]', m4)
    specs = [s for s in specs if s != '%%']
    if specs:
        with_params += 1
    for s in specs:
        spec[s] += 1
print('entries(by kind):', dict(kinds))
print('error codes (excluding OTHER_MSG_FMT):', total, 'min', min(codes), 'max', max(codes))
print('M1 == -1 (client sees OB code itself):', own_code, ' mapped to MySQL errno:', mysql_mapped)
print('entries with M4 != M3 (parameterised user message):', m3_ne_m4)
print('entries whose user message has printf params:', with_params)
print('distinct SQLSTATEs:', len(sqlstates))
print('printf specifiers in M4:', spec.most_common())
