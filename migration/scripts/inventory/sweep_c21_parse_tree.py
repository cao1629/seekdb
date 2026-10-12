#!/usr/bin/env python3
"""c21-parse-tree: ParseNode handling outside the parser (RULEBOOK 0.12 ParseNode<'p> 句柄, §2.9).

Scope: first-party files outside the parser directories (src/sql/parser, src/pl/parser, src/sql/pl/parser,
src/query/api/query/parser), comment/string-stripped text; method of the parser memo §2.3 / §2.6 [C4]
(parsenode_writes.py), restricted for C/D to files that mention ParseNode.
  A  `const_cast<ParseNode` / `const_cast<const ParseNode` / `const_cast<_ParseNode` (memo: 39)
  B  stack-constructed nodes: `ParseNode x;`, `ParseNode x[N];`, `ParseNode x = ...` inside a function
     body (memo: ~35; RULEBOOK 0.12 LocalParseNode)
  C  writes to the non-scalar (pointer) fields: `str_value_ =`, `raw_text_ =`, `children_ =`,
     `children_[i] =` (RULEBOOK 0.12: setters accepting only same-arena values; other sources copied in)
  D  node construction / copy calls: new_node, new_terminal_node, new_non_terminal_node, new_list_node,
     deep_copy_parse_node(_base), push_back_child, push_front_child, append_child, merge_tree
symbol: `<enclosing function>#<construct detail>` with `#N` for repeats in one file.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c21-parse-tree'
RX_A = re.compile(r'const_cast\s*<\s*(const\s+)?_?ParseNode\b\s*([*&]*)\s*>\s*\(')
RX_B = re.compile(r'(?<![\w:*&>])(?:const\s+)?ParseNode\s+([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)\s*(;|=|\{|,)')
RX_C = re.compile(r'([\w\]\)]+)\s*(->|\.)\s*(str_value_|raw_text_|children_)\s*(\[[^\]]*\])?\s*=(?!=)')
RX_D = re.compile(r'\b(new_node|new_terminal_node|new_non_terminal_node|new_list_node|deep_copy_parse_node|'
                  r'deep_copy_parse_node_base|push_back_child|push_front_child|append_child|merge_tree)\s*\(')


def main():
    rows = []
    seen = collections.Counter()

    def add(path, off, sym, cons, pre):
        key = (path, sym, cons)
        seen[key] += 1
        if seen[key] > 1:
            sym = '%s#%d' % (sym, seen[key])
        rows.append((path, sym, cons, L.line_of(path, off), pre))

    for p in L.first_party():
        if L.in_parser_dir(p):
            continue
        raw = L.raw(p)
        if 'ParseNode' not in raw:
            continue
        text = L.stripped(p)
        for m in RX_A.finditer(text):
            depth, j = 0, m.end() - 1
            k = j
            while k < len(text):
                if text[k] == '(':
                    depth += 1
                elif text[k] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            arg = re.sub(r'\s+', '', text[j + 1:k])[:60]
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#const_cast<%sParseNode%s>(%s)' % (fn, 'const ' if m.group(1) else '', m.group(2), arg),
                'const_cast<ParseNode>', '')
        for m in RX_B.finditer(text):
            off = m.start(1)
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if re.search(r'\btypedef\b', text[ls:m.start()]):
                continue
            if not L.in_function(p, off):
                continue
            fn = L.site_owner(p, off)
            arr = re.sub(r'\s+', '', m.group(2))
            add(p, off, '%s#ParseNode %s%s' % (fn, m.group(1), arr), 'stack-constructed ParseNode',
                'array' if arr else '')
        for m in RX_C.finditer(text):
            fn = L.site_owner(p, m.start())
            recv = m.group(1)
            field = m.group(3) + ('[]' if m.group(4) else '')
            pre = 'receiver:%s' % recv
            if re.search(r'node|tree|child|root|parse', recv, re.I):
                pre += ';receiver-looks-like-node'
            add(p, m.start(), '%s#%s%s%s=' % (fn, recv, m.group(2), field), 'write to ParseNode pointer field %s' % m.group(3), pre)
        for m in RX_D.finditer(text):
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            before = text[ls:m.start()]
            if before.lstrip().startswith('#') or re.search(r'[\w>*&]\s+\*?\s*$', before) and not re.search(
                    r'(=|return|\(|,|!)\s*$', before):
                continue  # a declaration or directive
            if re.search(r'(\.|->)\s*$', before):
                continue  # a member function of another class with the same name
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#%s()' % (fn, m.group(1)), 'ParseNode construction/copy call', '')
    n = L.finish(CAT, rows, 'A const_cast<ParseNode> (memo 39) + B stack-constructed ParseNode (~35) + C writes to '
                            'str_value_/raw_text_/children_ + D node construction/copy calls; outside parser dirs')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-50s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
