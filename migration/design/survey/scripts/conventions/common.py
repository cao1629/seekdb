# Shared helpers for the conventions survey (seekdb @ 7d907abfa).
# Run from the repository root with: python3 -I <script>
import os
import re
import subprocess

BASE = '7d907abfa'
CXX_EXT = ('.h', '.hpp', '.ipp', '.inl', '.cpp', '.cc', '.c')


def tracked_src_files():
    out = subprocess.run(['git', 'ls-tree', '-r', '--name-only', BASE, '--', 'src'],
                         capture_output=True, text=True, check=True).stdout.split('\n')
    return [p for p in out if p.endswith(CXX_EXT)]


def node_files():
    nodes = []
    with open('migration/depmap/nodes.tsv', encoding='utf-8') as f:
        next(f)
        for line in f:
            p = line.split('\t')[0]
            if p.endswith(CXX_EXT):
                nodes.append(p)
    return nodes


def read(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', errors='replace')


def strip_code(src, keep_strings=False):
    """Remove // and /* */ comments; blank string/char literal contents (unless keep_strings).
    Newlines are preserved so line numbers stay valid."""
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            if j < 0:
                j = n
            # handle line continuation inside // comment
            while j > 0 and j < n and src[j - 1] == '\\':
                k = src.find('\n', j + 1)
                if k < 0:
                    k = n
                j = k
            out.append(' ' * 0)
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in src[i:j]))
            i = j
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in src[i:j]))
            i = j
        elif c == 'R' and i + 1 < n and src[i + 1] == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_')):
            m = re.match(r'R"([^ ()\\\t\n]{0,16})\(', src[i:])
            if m:
                delim = ')' + m.group(1) + '"'
                j = src.find(delim, i + m.end())
                j = n if j < 0 else j + len(delim)
                if keep_strings:
                    out.append(src[i:j])
                else:
                    out.append('""' + ''.join('\n' if ch == '\n' else ' ' for ch in src[i + 2:j]))
                i = j
            else:
                out.append(c)
                i += 1
        elif c == '"' or (c == "'" and not (i > 0 and (src[i - 1].isalnum() and src[i - 1] not in 'uULR8') and i + 1 < n and src[i + 1].isdigit())):
            q = c
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == q or src[j] == '\n':
                    break
                j += 1
            j = min(j + 1, n)
            if keep_strings:
                out.append(src[i:j])
            else:
                inner = src[i + 1:j - 1]
                out.append(q + ' ' * len(inner) + (q if j - 1 > i and src[j - 1] == q else ''))
            i = j
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1
