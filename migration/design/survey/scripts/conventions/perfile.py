# Per-file breakdown of selected constructs (depmap nodes, comments/strings stripped).
# usage: python3 -I perfile.py <construct-name-from-scan.py> [<raw>]
import collections
import sys

sys.path.insert(0, '/tmp/conv-survey-7d907abfa-38738/scripts')
from common import tracked_src_files, node_files, read, strip_code, line_of  # noqa: E402
import scan  # noqa: E402

names = sys.argv[1].split('||')
raw = len(sys.argv) > 2 and sys.argv[2] == 'raw'
files = tracked_src_files() if raw else node_files()
top = int(sys.argv[3]) if len(sys.argv) > 3 else 40
for name in names:
    rx = scan.P[name]
    per = collections.Counter()
    first = {}
    for f in files:
        try:
            t = read(f)
        except FileNotFoundError:
            continue
        s = t if raw else strip_code(t)
        ms = list(rx.finditer(s))
        if ms:
            per[f] = len(ms)
            first[f] = line_of(s, ms[0].start())
    print(f'== {name}: {sum(per.values())} matches in {len(per)} files ({"raw" if raw else "code"})')
    for f, c in per.most_common(top):
        print(f'{c:6d}  {f}:{first[f]}')
