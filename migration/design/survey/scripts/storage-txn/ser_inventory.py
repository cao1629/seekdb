#!/usr/bin/env python3
"""Count serialization declarations per directory (headers only, stripped mirror).

A "serializable type" = a header-level declaration of one of:
  OB_UNIS_VERSION(...), OB_UNIS_VERSION_V(...), OB_UNIS_VERSION_PV(),
  NEED_SERIALIZE_AND_DESERIALIZE, or a hand-written
  `int serialize(char *buf, const int64_t buf_len, int64_t &pos) const`.
Each match is counted once per declaration site (approximates one type).
Also counts raw-struct disk images: reinterpret_cast<...Header...*> sites.

Usage: python3 -I ser_inventory.py <mirror_root> <dir> [<dir> ...]
"""
import collections
import os
import re
import sys

DECL = re.compile(
    r'\bOB_UNIS_VERSION(_V|_PV)?\s*\(|\bNEED_SERIALIZE_AND_DESERIALIZE\b|'
    r'\bint\s+serialize\s*\(\s*char\s*\*\s*\w+\s*,\s*const\s+int64_t\s+\w+\s*,\s*int64_t\s*&\s*\w+\s*\)\s*const')
HDR_CAST = re.compile(r'reinterpret_cast\s*<\s*(const\s+)?\w*(Header|HEADER)\w*\s*\*\s*>')


def main():
    root = sys.argv[1]
    for d in sys.argv[2:]:
        decl = 0
        files_with = 0
        casts = 0
        for dp, _, fns in os.walk(os.path.join(root, d)):
            for fn in fns:
                p = os.path.join(dp, fn)
                with open(p, encoding='utf-8', errors='replace') as fh:
                    s = fh.read()
                if fn.endswith(('.h', '.ipp', '.hpp')):
                    n = len(DECL.findall(s))
                    decl += n
                    files_with += 1 if n else 0
                casts += len(HDR_CAST.findall(s))
        print(f"{d}\tser_decls={decl}\theader_files_with_decl={files_with}\treinterpret_cast<*Header*>={casts}")


if __name__ == '__main__':
    main()
