#!/usr/bin/env python3
"""Count out-of-line (de)serialization function bodies and their lines per directory.

Same body detection as migration/feasibility/scripts/arch/func_share2.py; a
body counts when the 160 chars before its '{' contain a function name matching
serialize_/deserialize_/get_serialize_size_ variants, plus the OB_SERIALIZE_MEMBER /
OB_DEF_SERIALIZE / DEFINE_SERIALIZE macro invocations (counted as one function each).

Usage: python3 -I ser_funcs.py <mirror_root> <dir> [<dir> ...]
"""
import os
import re
import sys

NS = re.compile(r'(\bnamespace\s*[\w:]*\s*|\bextern\s*""\s*)$')
QUAL = re.compile(r'(\)|\bconst|\boverride|\bfinal|\bnoexcept|\bvolatile)\s*$')
NAME = re.compile(r'\b(serialize_?|deserialize_?|get_serialize_size_?|serialize_\w+|deserialize_\w+)\s*\([^;{}]*\)\s*(const)?\s*$')
MACRO = re.compile(r'\b(OB_SERIALIZE_MEMBER\w*|OB_DEF_SERIALIZE\w*|OB_DEF_DESERIALIZE\w*|DEFINE_SERIALIZE|DEFINE_DESERIALIZE|DEFINE_GET_SERIALIZE_SIZE|OB_UNIS_DEF_SERIALIZE\w*)\s*\(')


def scan(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        s = fh.read()
    funcs = lines = 0
    i, n, depth = 0, len(s), 0
    stack = []
    while i < n:
        c = s[i]
        if c == '{':
            pre = s[max(0, i - 160):i]
            if NS.search(pre):
                stack.append(True)
                i += 1
                continue
            if depth == 0 and QUAL.search(pre):
                d, j = 0, i
                while j < n:
                    if s[j] == '{':
                        d += 1
                    elif s[j] == '}':
                        d -= 1
                        if d == 0:
                            break
                    j += 1
                if NAME.search(pre) or MACRO.search(pre[-80:]):
                    funcs += 1
                    lines += s.count('\n', i, j + 1) + 1
                i = j + 1
                continue
            stack.append(False)
            depth += 1
        elif c == '}':
            if stack and not stack.pop():
                depth -= 1
        i += 1
    macros = len(MACRO.findall(s))
    return funcs, lines, macros


def main():
    root = sys.argv[1]
    for d in sys.argv[2:]:
        f = l = m = 0
        for dp, _, fns in os.walk(os.path.join(root, d)):
            for fn in fns:
                if fn.endswith(('.cpp', '.ipp', '.h')):
                    a, b, c = scan(os.path.join(dp, fn))
                    f += a
                    l += b
                    m += c
        print(f'{d}\tser_func_bodies={f}\tser_body_lines={l}\tser_macro_invocations={m}')


if __name__ == '__main__':
    main()
