#!/usr/bin/env python3
"""Translation surface: occurrences and files per IR type token across src/.
Counts `Type *`, `Type *&`, `Type &` token uses (types matched as whole words, any subclass name
matched by the given regex). Usage: python3 -I surface.py <repo_root>
"""
import os, re, sys, collections
root = sys.argv[1]
pats = {
    'raw expr (Ob*RawExpr)': re.compile(r'\bOb\w*RawExpr\b(?!Factory|Copier|Utils|Visitor|Replacer|UniqueSet|Pointer|PrintVisitor|ResType|Printer|Info|Checker|Getter)'),
    'stmt (ObDMLStmt/ObSelectStmt/ObInsertStmt/...Stmt)': re.compile(r'\bOb(DML|Select|Insert|Update|Delete|DelUpd|Merge|InsertAll)Stmt\b'),
    'logical op (ObLog*)': re.compile(r'\bObLog(icalOperator|TableScan|Join|GroupBy|Sort|Exchange|Set|SubPlanFilter|SubPlanScan|Limit|Distinct|Material|Window\w*|Insert|Update|Delete|DelUpd|Values|ExprValues|TempTable\w*|Granule\w*|JoinFilter|ForUpdate|FunctionTable|JsonTable|Topk|StatCollector|SelectInto|Expand|MonitoringDump|OptimizerStatsGathering|ValuesTableAccess)\b'),
    'runtime expr (ObExpr)': re.compile(r'\bObExpr\b'),
    'op spec (Ob*Spec)': re.compile(r'\bOb\w+Spec\b'),
    'operator (ObOperator / Ob*Op)': re.compile(r'\bOb(Operator|\w+Op)\b'),
}
occ = collections.Counter(); files = collections.defaultdict(set)
by_mod = collections.defaultdict(collections.Counter)
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.h', '.cpp', '.ipp')):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        mod = rel.split('/')[1]
        t = open(p, encoding='utf-8', errors='replace').read()
        for k, rx in pats.items():
            n = len(rx.findall(t))
            if n:
                occ[k] += n; files[k].add(rel); by_mod[k][mod] += n
for k in pats:
    mods = ', '.join(f'{m}={c}' for m, c in by_mod[k].most_common(6))
    print(f'{k:52s} occ={occ[k]:6d} files={len(files[k]):5d}  [{mods}]')
