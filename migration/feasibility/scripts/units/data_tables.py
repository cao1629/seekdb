#!/usr/bin/env python3
"""Heuristic: files dominated by literal data rows (string/number/brace rows)
or identifier table rows. Comments stripped first.
Usage: python3 -I data_tables.py REPO OUT_TSV"""
import os, re, sys
repo, out = sys.argv[1], sys.argv[2]
EXTS = {".c", ".h", ".cc", ".cpp", ".hpp", ".ipp", ".def", ".map"}
CMT = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"', re.S)
def strip(m):
    s = m.group(0)
    if s.startswith('"'):
        return s
    return "" if s.startswith("//") else " " + "\n" * s.count("\n")
NUM = r'[-+]?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)[uUlLfF]*'
STR = r'(?:u8|L|u|U)?"(?:[^"\\]|\\.)*"'
LIT = r'(?:%s|%s|\'(?:[^\'\\]|\\.)+\')' % (NUM, STR)
LITROW = re.compile(r'^\s*(?:\{\s*)*%s(?:\s*\}*\s*,\s*\{*\s*%s)*\s*\}*\s*,?\s*\}*\s*,?\s*$' % (LIT, LIT))
IDENT = r'[A-Za-z_][A-Za-z0-9_:]*'
TOK = r'(?:%s|%s)' % (LIT, IDENT)
IDROW = re.compile(r'^\s*(?:\{\s*)*%s(?:\s*\}*\s*,\s*\{*\s*%s)*\s*\}*\s*,?\s*\}*\s*,?\s*$' % (TOK, TOK))
XROW = re.compile(r'^\s*[A-Z_][A-Z0-9_]*\s*\(.*\)\s*[,;]?\s*$')
rows = []
for dp, dns, fns in os.walk(os.path.join(repo, "src")):
    for fn in fns:
        if os.path.splitext(fn)[1] not in EXTS:
            continue
        p = os.path.join(dp, fn)
        with open(p, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        n = raw.count("\n")
        if n < 1000 and not fn.endswith((".map", ".def")):
            continue
        ls = [l for l in CMT.sub(strip, raw).split("\n") if l.strip()]
        lit = sum(1 for l in ls if LITROW.match(l))
        idr = sum(1 for l in ls if not LITROW.match(l) and (IDROW.match(l) or XROW.match(l)))
        nb = len(ls) or 1
        rows.append((os.path.relpath(p, repo), n, lit, idr, round(lit / nb, 2), round((lit + idr) / nb, 2)))
rows.sort(key=lambda r: -r[1])
with open(out, "w") as f:
    f.write("# path\tlines\tliteral_rows\tident_or_xmacro_rows\tliteral_frac\ttable_frac\n")
    for r in rows:
        f.write("%s\t%d\t%d\t%d\t%.2f\t%.2f\n" % r)
for r in rows:
    if r[4] >= 0.5 or r[5] >= 0.7:
        print("%-78s %7d lit=%7d id=%6d lit%%=%.2f table%%=%.2f" % r)
