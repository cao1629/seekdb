# Build a comment- and string-literal-stripped mirror of seekdb src/ (read-only on repo).
# Newlines are preserved so line numbers still match the original files.
# Usage: python3 -I strip.py <repo_root> <ignore_list> <out_dir>
import os, sys

EXTS = ('.cpp', '.h', '.c', '.cc', '.hpp', '.ipp', '.inc', '.def', '.hh', '.cxx')

def strip(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ''
        if c == '/' and nx == '/':
            j = src.find('\n', i)
            if j < 0:
                j = n
            # line continuation inside // comment is rare; ignore
            i = j
            continue
        if c == '/' and nx == '*':
            j = src.find('*/', i + 2)
            if j < 0:
                j = n - 2
            out.append('\n' * src.count('\n', i, j + 2))
            i = j + 2
            continue
        if c == 'R' and nx == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_')) or \
           (c in 'uUL8' and src.startswith('R"', i + 1) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_'))):
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
            if prev.isdigit() and nx.isalnum():  # digit separator
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
    root, ign, outd = sys.argv[1], sys.argv[2], sys.argv[3]
    ignored = set(l.strip() for l in open(ign) if l.strip())
    nfiles = nlines = 0
    for dp, dn, fn in os.walk(os.path.join(root, 'src')):
        for f in fn:
            if not f.endswith(EXTS):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root)
            if rel in ignored:
                continue
            with open(full, 'r', encoding='utf-8', errors='replace') as fh:
                s = fh.read()
            o = os.path.join(outd, rel)
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with open(o, 'w', encoding='utf-8') as fh:
                fh.write(strip(s))
            nfiles += 1
            nlines += s.count('\n')
    print('stripped files', nfiles, 'lines', nlines)

main()
