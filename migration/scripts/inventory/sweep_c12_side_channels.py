#!/usr/bin/env python3
"""c12-side-channels: cross-operator side channels and operator state read/written by other code
(RULEBOOK 0.6 执行期: ObExecContext id-indexed tables; 迭代器不得持有任何指向算子的指针).

Patterns (comment/string-stripped first-party text; see README.md, section c12-side-channels):
  A  ir-graphs memo method: `get_operator_kit(` call sites outside src/sql/engine/ob_exec_context.*
     and src/sql/engine/ob_operator.cpp (memo: 28 on raw text; one of the 28 sits in a block comment,
     ob_table_scan_with_index_back_op.cpp:43, so the stripped count is 27)
  B  data members of operator type (`ObOperator` / `Ob*Op`, pointer or reference) declared in a class
     that is not itself an operator, i.e. whose base clause names neither `ObOperator` nor a base whose
     name ends in `Op` (iterators, contexts, helpers holding operators), under src/sql, src/query and
     src/pl; the ObOperator tree links in ob_operator.h (parent_/child_/left_/right_) are excluded
symbol: A `<enclosing function>#get_operator_kit(<id expr>)`; B `<class>::<member>`.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c12-side-channels'
RX_KIT = re.compile(r'\bget_operator_kit\s*\(')
RX_OP_MEMBER = re.compile(r'(?m)^[ \t]*((?:const\s+)?((?:\w+::)*(?:ObOperator|Ob\w+Op))\s*(\*|&)\s*(?:const\s+)?)([A-Za-z_]\w*)\s*;')
KIT_EXCLUDE = ('src/sql/engine/ob_exec_context.', 'src/sql/engine/ob_operator.cpp')
B_DIRS = ('src/sql/', 'src/query/', 'src/pl/')
TREE_LINKS = {('src/query/api/query/engine/ob_operator.h', 'ObOperator')}


def call_args(text, open_paren):
    depth, i = 0, open_paren
    while i < len(text):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
        i += 1
    return text[open_paren + 1:]


def main():
    rows = []
    seen = collections.Counter()

    def add(path, off, sym, cons, pre):
        key = (path, sym, cons)
        seen[key] += 1
        if seen[key] > 1:
            sym = '%s#%d' % (sym, seen[key])
        rows.append((path, sym, cons, L.line_of(path, off), pre))

    files = L.first_party()
    for p in files:
        if 'get_operator_kit' not in L.raw(p) or p.startswith(KIT_EXCLUDE):
            continue
        text = L.stripped(p)
        for m in RX_KIT.finditer(text):
            arg = re.sub(r'\s+', '', call_args(text, m.end() - 1))
            fn = L.site_owner(p, m.start())
            pre = 'px' if p.startswith('src/sql/engine/px/') else ''
            add(p, m.start(), '%s#get_operator_kit(%s)' % (fn, arg), 'operator lookup by id outside the operator', pre)
    for p in files:
        if not p.startswith(B_DIRS) or 'Op' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_OP_MEMBER.finditer(text):
            off = m.start(4)
            cls = L.enclosing_class(p, off)
            if cls is None:
                continue
            leaf = cls.split('::')[-1]
            cscope = [s for s in L.scope_chain(p, off) if s.kind == 'class'][-1]
            head = ' '.join(cscope.header.split())
            bases = head.split(':', 1)[1] if re.search(r'(?<!:):(?!:)', head) else ''
            bases = re.sub(r'<[^<>]*>', '', bases)
            if re.search(r'\b(ObOperator|\w+Op)\b', bases):
                continue  # an operator class: its operator links are the operator tree itself
            if (p, leaf) in TREE_LINKS:
                continue
            kind = 'reference' if m.group(3) == '&' else 'pointer'
            add(p, off, '%s::%s' % (cls, m.group(4)), 'operator %s held by non-operator class' % kind,
                'type:%s' % m.group(2).split('::')[-1])
    n = L.finish(CAT, rows, 'A get_operator_kit lookups outside exec ctx/operator (memo method, 27 code sites) '
                            '+ B operator pointers/references held by non-operator classes')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-50s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
