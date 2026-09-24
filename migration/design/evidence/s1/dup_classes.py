#!/usr/bin/env python3
"""Class and struct names defined with a body in more than one tracked header under src/."""
import collections, re, subprocess
root = '/Users/colin/seekdb-dev/migrate-to-rust'
files = [f for f in subprocess.run(['git', '-C', root, 'ls-files', 'src'], capture_output=True, text=True).stdout.split() if f.endswith(('.h', '.hpp', '.ipp'))]
decl = re.compile(r'^\s*(?:class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\s*(?:final\s*)?(?::[^;{]*)?\{', re.M)
where = collections.defaultdict(set)
for f in files:
    for mm in decl.finditer(open(f'{root}/{f}', encoding='utf-8', errors='replace').read()):
        where[mm.group(1)].add(f)
dups = {k: v for k, v in where.items() if len(v) > 1}
print(len(dups))
for k, v in sorted(dups.items(), key=lambda t: -len(t[1]))[:10]: print(k, len(v))
