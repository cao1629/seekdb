#!/usr/bin/env python3
"""Line/file counts per subdirectory under given roots (C/C++ sources only)."""
import os
import sys

EXTS = ('.h', '.hpp', '.cpp', '.cc', '.c', '.ipp', '.inl')


def count(root):
    lines = 0
    files = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith(EXTS):
                files += 1
                with open(os.path.join(dp, fn), 'rb') as f:
                    lines += sum(1 for _ in f)
    return lines, files


def main():
    for root in sys.argv[1:]:
        rows = []
        tot_l, tot_f = 0, 0
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                l, f = count(p)
                rows.append((l, f, p))
            elif name.endswith(EXTS):
                with open(p, 'rb') as fh:
                    l = sum(1 for _ in fh)
                rows.append((l, 1, p))
        for l, f, p in sorted(rows, reverse=True):
            print(f"{l:9d} {f:5d} {p}")
            tot_l += l
            tot_f += f
        print(f"{tot_l:9d} {tot_f:5d} TOTAL {root}")


if __name__ == '__main__':
    main()
