# Print a function body from the original source starting at the '{' on/after the given line.
# usage: python3 -I showfn.py path:line [maxlines]
import sys

spec = sys.argv[1]
maxl = int(sys.argv[2]) if len(sys.argv) > 2 else 80
path, line = spec.rsplit(':', 1)
line = int(line)
strip = open('/tmp/conv-survey-7d907abfa-38738/stripped/' + path, encoding='utf-8').read().split('\n')
orig = open(path, encoding='utf-8', errors='replace').read().split('\n')
# find brace depth from stripped text
start = line - 1
d = 0
end = None
for i in range(start, len(strip)):
    for ch in strip[i]:
        if ch == '{':
            d += 1
        elif ch == '}':
            d -= 1
            if d == 0:
                end = i
                break
    if end is not None:
        break
lo = max(0, start - 3)
hi = min(end + 1 if end is not None else start + maxl, lo + maxl)
for i in range(lo, hi):
    print(f'{i + 1:5d}  {orig[i]}')
if end is not None and end + 1 > hi:
    print(f'... ({end + 1 - hi} more lines)')
