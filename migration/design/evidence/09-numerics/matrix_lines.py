# Classify the lines of the four arithmetic matrix .result files.
# Run from tools/deploy/mysql_test of the frozen tree (834bbee1e).
import re, collections
FILES = ['r/mysql/add.result', 'test_suite/datatype/r/mysql/minus.result',
         'test_suite/datatype/r/mysql/div.result', 'test_suite/expr/r/mysql/mul.result']
STMT = re.compile(r'^(select|insert|create|drop|set|desc|show|use|alter|update|delete|explain)\b', re.I)
COLKIND = {'c1':'int','c2':'int','c3':'int','c4':'int','c5':'int','c6':'dec','c7':'dec','c8':'dec',
           'c9':'str','c10':'time','c11':'time','c12':'time','c13':'int','c14':'str','c15':'str','c16':'str'}
def kind(op):
    op = op.strip()
    if op in COLKIND: return COLKIND[op]
    if op.upper() == 'NULL': return 'null'
    if op[:1] in '"\'': return 'str'
    if re.fullmatch(r'-?\d+', op): return 'int'
    if re.fullmatch(r'-?\d*\.\d+', op): return 'dec'
    return 'other'
def shape(l):
    if l == 'NULL': return 'NULL'
    if l.startswith('ERROR'): return 'error'
    if re.fullmatch(r'-?\d+(\.\d+)?e[+-]?\d+', l): return 'exponent'
    if re.fullmatch(r'-?\d+\.\d+', l): return 'fixed'
    if re.fullmatch(r'-?\d+', l): return 'integer'
    return 'other'
by_line = collections.Counter(); by_operand = collections.Counter(); total = 0
for f in FILES:
    lines = open(f, encoding='utf-8', errors='replace').read().split('\n')
    if lines and lines[-1] == '': lines = lines[:-1]
    total += len(lines); cur = None
    for l in lines:
        m = re.match(r'^select \((.+?) (\+|-|\*|div|/) (.+)\) from \w+;$', l)
        if m:
            ks = {kind(m.group(1)), kind(m.group(3))}
            cur = ('string operand' if 'str' in ks else 'temporal operand' if 'time' in ks else
                   'decimal operand' if 'dec' in ks else 'NULL literal' if 'null' in ks else
                   'integer operands only' if ks <= {'int'} else 'other')
            by_line['statement'] += 1; continue
        if STMT.match(l): by_line['statement'] += 1; cur = None; continue
        if l.startswith('(') and l.endswith(')'): by_line['column header'] += 1; continue
        s = shape(l); by_line[s] += 1
        if cur: by_operand[(cur, s)] += 1
print('total lines', total); print(dict(by_line))
for c in sorted({k[0] for k in by_operand}):
    print(c, {s: by_operand[(c, s)] for s in ['NULL','integer','fixed','exponent','error','other']})
