# Cache comment-stripped (strings blanked) and comment-stripped (strings kept) copies of depmap node files.
# usage: python3 -I cache.py   (run from repo root)
import os
import sys

sys.path.insert(0, '/tmp/conv-survey-7d907abfa-38738/scripts')
from common import node_files, read, strip_code  # noqa: E402

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
ROOT_S = '/tmp/conv-survey-7d907abfa-38738/stripped_keepstr'
n = 0
for f in node_files():
    try:
        t = read(f)
    except FileNotFoundError:
        continue
    for root, keep in ((ROOT, False), (ROOT_S, True)):
        out = os.path.join(root, f)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as g:
            g.write(strip_code(t, keep_strings=keep))
    n += 1
print('cached', n)
