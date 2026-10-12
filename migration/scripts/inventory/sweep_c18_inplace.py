#!/usr/bin/env python3
"""c18-inplace: in-place writes into borrowed bytes (RULEBOOK §2.4 就地改写借来的字节, 0.12, §2.9).

Sites (comment/string-stripped first-party text, definitions and declarations excluded):
  A  calls `ObCharset::casedn(` / `ObCharset::caseup(` (they rewrite the ObString argument in place)
  B  every `const_cast<char *>` / `const_cast<char*>` (any spacing)
  C  calls of the in-place case helpers `str_tolower(` / `str_toupper(` (parser C, parse_node.c),
     `str_to_lower(` (ob_dml_resolver.h), `easy_string_toupper(` / `easy_string_tolower(`
pretag (hint only) classifies the written buffer from the operand text and the enclosing function:
  borrowed:parse-node   operand mentions str_value_ / raw_text_
  borrowed:member       operand is (a view of) a data member `x_`
  borrowed:param(T)     operand is (a view of) a parameter of the enclosing function
  local:stack-array     operand is a `char x[N]` declared in the enclosing function
  local:owned-buffer    operand is (a view of) a local ObSqlString / ObStringBuffer / std::string
  local:view            operand is (a view of) a local ObString (a view: whose bytes it shows is open)
  literal               operand is a string literal
symbol: `<enclosing function>#<call>(<operand>)` with a `#N` ordinal for repeats in one file.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402
from sweep_c11_scopes import split_macro_args  # noqa: E402

CAT = 'c18-inplace'
RX_A = re.compile(r'\bObCharset\s*::\s*(casedn|caseup)\s*\(')
RX_B = re.compile(r'\bconst_cast\s*<\s*char\s*\*\s*>\s*\(')
RX_C = re.compile(r'\b(str_tolower|str_toupper|str_to_lower|easy_string_toupper|easy_string_tolower)\s*\(')


def is_definition_or_decl(text, m, path):
    """True if the call-looking match is a function definition or declaration."""
    ls = L.line_start_offset(path, L.line_of(path, m.start()))
    before = text[ls:m.start()]
    return bool(re.search(r'[\w>*&]\s*$', before)) and not re.search(r'(=|return|\(|,|!|&&|\|\||\?|:)\s*$', before) \
        and not re.search(r'\b(if|while|OB_FAIL|OB_SUCC|OB_ISNULL|OB_NOT_NULL)\s*$', before)


def operand_base(expr):
    e = re.sub(r'\s+', '', expr)
    while e.startswith('(') and e.endswith(')') and split_macro_args(e, 0)[1] == len(e) - 1:
        e = e[1:-1]
    e = re.sub(r'^\((const)?char\*\)', '', e)
    m = re.match(r'^&?([A-Za-z_]\w*)(?:\.|->)(?:ptr|data|c_str|get_data|ptr_)\(\)$', e)
    if m:
        return m.group(1), True
    m = re.match(r'^&?([A-Za-z_]\w*)$', e)
    if m:
        return m.group(1), False
    m = re.match(r'^([A-Za-z_]\w*)(?:\.|->)', e)
    if m:
        return m.group(1), True
    return None, False


def classify(path, off, expr):
    e = re.sub(r'\s+', '', expr)
    if 'str_value_' in e or 'raw_text_' in e:
        return 'borrowed:parse-node'
    if re.match(r'^\(?""', e):
        return 'literal'
    base, via_view = operand_base(expr)
    if not base:
        return ''
    if base.endswith('_') or base == 'this':
        return 'borrowed:member'
    fn = L.function_scope(path, off)
    if fn is None:
        return ''
    header = ' '.join(fn.header.split())
    pm = re.search(r'([\w:]+(?:\s*<[^<>]*>)?)\s*([*&]+)?\s*\b%s\s*(?:[,)=\[])' % re.escape(base), header)
    if pm and pm.group(1) not in ('return',):
        t = pm.group(1).split('::')[-1] + (pm.group(2) or '')
        return 'borrowed:param(%s)' % t
    body = L.stripped(path)[fn.open:off]
    if re.search(r'\bchar\s+%s\s*\[' % re.escape(base), body):
        return 'local:stack-array'
    if re.search(r'\b(ObSqlString|ObStringBuffer|std::string|ObFastFormatInt|ObSqlStringBuffer)\s+%s\b' % re.escape(base), body):
        return 'local:owned-buffer'
    if re.search(r'\bObString\s+%s\b' % re.escape(base), body):
        return 'local:view'
    return ''


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
        raw = L.raw(p)
        if not ('const_cast' in raw or 'case' in raw or 'str_to' in raw or 'easy_string_to' in raw):
            continue
        text = L.stripped(p)
        for m in RX_A.finditer(text):
            if is_definition_or_decl(text, m, p):
                continue
            args, _ = split_macro_args(text, m.end() - 1)
            arg = args[1] if len(args) > 1 else ''
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#ObCharset::%s(%s)' % (fn, m.group(1), re.sub(r'\s+', '', arg)[:60]),
                'ObCharset::%s in-place case conversion' % m.group(1), classify(p, m.start(), arg))
        for m in RX_B.finditer(text):
            args, _ = split_macro_args(text, m.end() - 1)
            arg = args[0] if args else ''
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#const_cast<char*>(%s)' % (fn, re.sub(r'\s+', '', arg)[:60]),
                'const_cast<char *>', classify(p, m.start(), arg))
        for m in RX_C.finditer(text):
            if is_definition_or_decl(text, m, p):
                continue
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if text[ls:m.start()].lstrip().startswith('#'):
                continue
            args, _ = split_macro_args(text, m.end() - 1)
            arg = args[0] if args else ''
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#%s(%s)' % (fn, m.group(1), re.sub(r'\s+', '', arg)[:60]),
                'in-place case helper %s' % m.group(1), classify(p, m.start(), arg))
    n = L.finish(CAT, rows, 'A ObCharset::casedn/caseup calls + B const_cast<char *> + C str_tolower-style in-place '
                            'helpers; pretag = borrowed/local guess from the operand')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-50s %d' % (k, v))
    pre = collections.Counter(r[4].split('(')[0] for r in rows)
    print('  pretags:', dict(sorted(pre.items())))
    return n


if __name__ == '__main__':
    main()
