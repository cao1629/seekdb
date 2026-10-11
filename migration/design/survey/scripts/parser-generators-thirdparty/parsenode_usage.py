import re, sys, os, collections
# Usage: python3 -I parsenode_usage.py REPO FILELIST
# Counts ParseNode/ParseResult usage in tracked src files, split into parser dirs vs. the rest.
repo, flist = sys.argv[1], sys.argv[2]
files = [l.strip() for l in open(flist) if l.strip()]
excl_dirs = ('src/sql/parser/', 'src/pl/parser/', 'src/query/api/query/parser/')
pat = {
 'ParseNode': re.compile(r'\bParseNode\b'),
 'children_[': re.compile(r'children_\s*\['),
 '->type_/.type_': re.compile(r'(->|\.)type_\b'),
 'str_value_': re.compile(r'\bstr_value_\b'),
 '->value_/.value_': re.compile(r'(->|\.)value_\b'),
 'num_child_': re.compile(r'\bnum_child_\b'),
 'ParseResult': re.compile(r'\bParseResult\b'),
}
files_with = collections.Counter(); hits = collections.Counter(); mod_files = collections.Counter()
lines_with = collections.Counter()
for f in files:
    if not f.startswith('src/'):
        continue
    p = os.path.join(repo, f)
    try:
        txt = open(p, encoding='utf-8', errors='replace').read()
    except FileNotFoundError:
        continue
    if not pat['ParseNode'].search(txt):
        continue
    inparser = f.startswith(excl_dirs)
    key = 'inside' if inparser else 'outside'
    files_with[key] += 1
    lines_with[key] += sum(1 for line in txt.splitlines() if pat['ParseNode'].search(line))
    if inparser:
        mod = '(parser dirs)'
    elif f.startswith('src/sql/') or f.startswith('src/pl/'):
        parts = f.split('/')
        mod = parts[1] + '/' + parts[2]
    else:
        mod = f.split('/')[1]
    mod_files[mod] += 1
    for k, rx in pat.items():
        hits[(key, k)] += len(rx.findall(txt))
print('files mentioning ParseNode:', dict(files_with), ' lines mentioning ParseNode:', dict(lines_with))
for k in pat:
    print(f'  {k:20s} outside={hits[("outside", k)]:6d} inside={hits[("inside", k)]:6d}')
print('files mentioning ParseNode by module/subdir:')
for m, c in mod_files.most_common(30):
    print(f'  {m:30s} {c}')
