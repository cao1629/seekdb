#!/usr/bin/env python3
"""Count `template <` declarations and lines per directory in the stripped mirror.

Usage: python3 -I templates.py <mirror_root> <dir> [<dir> ...]
"""
import os
import re
import sys

RX = re.compile(r'\btemplate\s*<')


def main():
    root = sys.argv[1]
    for d in sys.argv[2:]:
        t = 0
        n = 0
        for dp, _, fns in os.walk(os.path.join(root, d)):
            for fn in fns:
                with open(os.path.join(dp, fn), encoding='utf-8', errors='replace') as fh:
                    s = fh.read()
                t += len(RX.findall(s))
                n += s.count('\n')
        print(f'{d}\ttemplates={t}\tlines={n}\tper_kloc={1000.0 * t / max(n, 1):.1f}')


if __name__ == '__main__':
    main()
