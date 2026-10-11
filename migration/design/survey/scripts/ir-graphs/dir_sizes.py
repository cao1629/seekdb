#!/usr/bin/env python3
"""Files/lines per directory (C/C++ sources), excluding nothing.
Usage: python3 -I dir_sizes.py <repo_root> dir1 [dir2 ...]
"""
import os, sys
root = sys.argv[1]
for d in sys.argv[2:]:
    n = 0; l = 0
    for dp, dn, fn in os.walk(os.path.join(root, d)):
        for f in fn:
            if f.endswith(('.h', '.cpp', '.ipp', '.c')):
                n += 1
                with open(os.path.join(dp, f), 'rb') as fh:
                    l += fh.read().count(b'\n')
    print(f'{d:32s} files={n:5d} lines={l:8d}')
