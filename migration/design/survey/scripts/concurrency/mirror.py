# Build a comment/string-stripped mirror of the depmap node files (translation scope).
# Newlines preserved so line numbers match. Usage: python3 -I mirror.py <repo> <outdir>
import os, sys


def strip(src):
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ''
        if c == '/' and nx == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            i = j
            continue
        if c == '/' and nx == '*':
            j = src.find('*/', i + 2)
            j = n - 2 if j < 0 else j
            out.append('\n' * src.count('\n', i, j + 2))
            i = j + 2
            continue
        if c == 'R' and nx == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_')):
            k = src.find('"', i)
            p = src.find('(', k)
            delim = src[k + 1:p]
            if p > 0 and len(delim) <= 16 and '\n' not in delim:
                end = src.find(')' + delim + '"', p)
                if end > 0:
                    out.append('""' + '\n' * src.count('\n', i, end))
                    i = end + len(delim) + 2
                    continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != '\n':
                if src[j] == '\\':
                    j += 1
                j += 1
            out.append('""')
            i = j + 1
            continue
        if c == "'":
            prev = src[i - 1] if i > 0 else ''
            if prev.isdigit() and nx.isalnum():
                out.append(c)
                i += 1
                continue
            j = i + 1
            while j < n and src[j] != "'" and src[j] != '\n':
                if src[j] == '\\':
                    j += 1
                j += 1
            out.append("' '")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def main():
    repo, outd = sys.argv[1], sys.argv[2]
    lines = open(os.path.join(repo, 'migration/depmap/nodes.tsv')).read().splitlines()[1:]
    nodes = [l.split('\t')[0] for l in lines if l]
    cnt = 0
    for rel in nodes:
        p = os.path.join(repo, rel)
        s = open(p, encoding='utf-8', errors='replace').read()
        o = os.path.join(outd, rel)
        os.makedirs(os.path.dirname(o), exist_ok=True)
        with open(o, 'w') as fh:
            fh.write(strip(s))
        cnt += 1
    print('mirrored', cnt, 'files')


main()
