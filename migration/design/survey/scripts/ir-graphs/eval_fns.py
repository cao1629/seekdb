#!/usr/bin/env python3
"""Count runtime eval function definitions (the ObExpr::EvalFunc / EvalBatchFunc shapes) and
`ObExprOperator` subclasses per file, plus template-instantiated function tables.
Usage: python3 -I eval_fns.py <repo_root>
"""
import os, re, sys, collections
root = sys.argv[1]
src = os.path.join(root, 'src')
row = re.compile(r'\b(?:int|OB_INLINE int|inline int|static int)\s+([\w:<>]+)\s*\(\s*const\s+(?:sql::)?ObExpr\s*&\s*\w*\s*,\s*(?:sql::)?ObEvalCtx\s*&\s*\w*\s*,\s*(?:common::)?ObDatum\s*&\s*\w*\s*\)\s*(?:const\s*)?\{', re.S)
batch = re.compile(r'\b(?:int|OB_INLINE int|inline int|static int)\s+([\w:<>]+)\s*\(\s*const\s+(?:sql::)?ObExpr\s*&\s*\w*\s*,\s*(?:sql::)?ObEvalCtx\s*&\s*\w*\s*,\s*const\s+(?:sql::)?ObBitVector\s*&\s*\w*\s*,\s*const\s+int64_t\s+\w*\s*\)\s*(?:const\s*)?\{', re.S)
macro_row = re.compile(r'\bint\s+([\w:<>]+)\s*\(\s*EVAL_FUNC_ARG_DECL\s*\)\s*\{')
macro_batch = re.compile(r'\bint\s+([\w:<>]+)\s*\(\s*BATCH_EVAL_FUNC_ARG_DECL\s*\)\s*\{')
c = collections.Counter()
for dp, dn, fn in os.walk(src):
    for f in fn:
        if not f.endswith(('.h', '.cpp', '.ipp')):
            continue
        t = open(os.path.join(dp, f), encoding='utf-8', errors='replace').read()
        c['row_eval_defs'] += len(row.findall(t)) + len(macro_row.findall(t))
        c['batch_eval_defs'] += len(batch.findall(t)) + len(macro_batch.findall(t))
        if 'ser_eval' in t or 'REG_SER_FUNC' in t:
            c['files_mentioning_ser_func'] += 1
for k, v in c.items():
    print(k, v)
