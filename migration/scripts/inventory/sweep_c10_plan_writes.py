#!/usr/bin/env python3
"""c10-plan-writes: runtime writes to the shared physical plan (RULEBOOK 0.6 "运行期写共享计划的地方").

Patterns (on comment/string-stripped first-party text; see README.md, section c10-plan-writes):
  A  write through a `const ObExpr &x` / `const ObExpr *x` parameter inside that function's body
     (`x.member[..] = ..`, `x->member = ..`, `x.args_[i]->member = ..`, ++/--), and writes to
     ObExpr pointer-array members inside `ObExpr::f(...) const` bodies. Reproduces and generalises
     the confirmed ObExprColumnConv::eval_enumset site (ob_expr_column_conv.cpp:741-746).
  B  ir-graphs memo pattern, verbatim scope (src/sql/engine/**/*.cpp):
       static_cast<\s*(Ob|[A-Z])\w*\s*\*\s*>\s*\(\s*expr\.extra_info_\s*\)          (memo: 31)
  C  broadened: any non-const static_cast/reinterpret_cast/C-style cast of `<e>.extra_info_` or
     `<e>->extra_info_`, all first-party files, minus B
  D  ir-graphs memo pattern, verbatim scope (src/sql/engine, src/sql/das, src/storage; .cpp/.h):
       const_cast<\s*(const\s+)?Ob\w*Spec\s*[*&]                                     (memo: 6)
  E  broadened: const_cast to ObExpr / ObExprFrameInfo / ObPhysicalPlan / *Spec / ObSqlExpression /
     *ExtraInfo, all first-party files, minus D
  F  `mutable` data members in engine headers (src/sql/engine, src/query/api/query/engine)
symbol: `<enclosing function>#<discriminator>` (A/B/C/D/E) or `<class>::<member>` (F); a `#N`
ordinal is appended when the same symbol repeats in one file.
"""
import collections
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c10-plan-writes'

RX_PARAM = re.compile(r'\bconst\s+(?:(?:oceanbase::)?sql::)?ObExpr\s*([&*])\s*(\w+)\s*[,)=]')
RX_B = re.compile(r'static_cast<\s*(Ob|[A-Z])\w*\s*\*\s*>\s*\(\s*expr\.extra_info_\s*\)')
RX_C = re.compile(r'(?:static_cast|reinterpret_cast)\s*<\s*(?!const\b)([\w:]+)\s*\*\s*>\s*\(\s*([\w.\->\[\]]*?)\s*(?:\.|->)\s*extra_info_\s*\)'
                  r'|\(\s*(?!const\b)([A-Z][\w:]*)\s*\*\s*\)\s*\(?\s*([\w.\->\[\]]*?)\s*(?:\.|->)\s*extra_info_\b')
RX_D = re.compile(r'const_cast<\s*(const\s+)?Ob\w*Spec\s*[*&]')
RX_E = re.compile(r'const_cast\s*<\s*(?:const\s+)?((?:\w+::)*(?:ObExpr|ObExprFrameInfo|ObPhysicalPlan|Ob\w*Spec|ObSqlExpression|'
                  r'ObIExprExtraInfo|Ob\w*ExtraInfo))\s*[*&]')
RX_F = re.compile(r'(?m)^[ \t]*mutable\s+([^;{}()]+?)\s+(\w+)\s*(?:\[[^\]]*\])?\s*;')
RX_OBEXPR_CONST_WRITE = re.compile(r'(?<![\w.>])(?:this\s*->\s*)?(args_|parents_|inner_functions_)\s*\[[^\]]*\]\s*=(?!=)')
MEMO_B_DIR = 'src/sql/engine/'
MEMO_D_DIRS = ('src/sql/engine/', 'src/sql/das/', 'src/storage/')
F_DIRS = ('src/sql/engine/', 'src/query/api/query/engine/')


def cg_hint(path, fn):
    if path.startswith('src/sql/code_generator/'):
        return 'in-codegen'
    base = (fn or '').split('(')[0].split('::')[-1]
    if re.match(r'^(cg_\w*|generate_\w*|construct_\w*|init_\w*|set_\w*|deserialize\w*|assign\w*|deep_copy\w*)$', base):
        return 'cg-or-setup-fn:' + base
    return ''


def main():
    rows = []
    seen_sym = collections.Counter()

    def add(path, off, sym, cons, pre):
        line = L.line_of(path, off)
        key = (path, sym, cons)
        seen_sym[key] += 1
        if seen_sym[key] > 1:
            sym = '%s#%d' % (sym, seen_sym[key])
        rows.append((path, sym, cons, line, pre))

    files = L.first_party()
    # --- A: writes through const ObExpr& parameters / in const ObExpr members
    for p in files:
        if 'ObExpr' not in L.raw(p):
            continue
        text = L.stripped(p)
        for s in L.scopes(p):
            if s.kind != 'function':
                continue
            names = {m.group(2): m.group(1) for m in RX_PARAM.finditer(s.header)}
            body = text[s.open:s.close]
            fn = None
            if names:
                alt = '|'.join(sorted(map(re.escape, names)))
                rx = re.compile(r'(?<![\w.>])(%s)\s*(\.|->)\s*(\w+)\s*((?:\[[^\]]*\])*)\s*(?:->\s*(\w+)\s*((?:\[[^\]]*\])*)\s*)?'
                                r'(=(?!=)|\+=|-=|\|=|&=|\+\+|--)|(\+\+|--)\s*(%s)\s*(\.|->)\s*(\w+)' % (alt, alt))
                for m in rx.finditer(body):
                    off = s.open + m.start()
                    if fn is None:
                        fn = L.enclosing_function(p, s.open + 1) or s.name
                    if m.group(1):
                        lhs = m.group(1) + m.group(2) + m.group(3) + re.sub(r'\s+', '', m.group(4) or '')
                        if m.group(5):
                            lhs += '->' + m.group(5) + re.sub(r'\s+', '', m.group(6) or '')
                        op = m.group(7)
                    else:
                        lhs = m.group(9) + m.group(10) + m.group(11)
                        op = m.group(8)
                    add(p, off, '%s#%s%s' % (fn, lhs, op if op in ('++', '--') else '='),
                        'write through const ObExpr& at eval time', 'param:%s' % m.group(1 if m.group(1) else 9))
            if re.search(r'(?:^|::)ObExpr::\w+$', s.name) and re.search(r'\)\s*const\s*$', ' '.join(s.header.split())):
                for m in RX_OBEXPR_CONST_WRITE.finditer(body):
                    fn = fn or L.enclosing_function(p, s.open + 1) or s.name
                    add(p, s.open + m.start(), '%s#%s[]=' % (fn, m.group(1)),
                        'write to ObExpr member in const member function', '')
    # --- B / C: extra_info_ conversions
    b_keys = set()
    for p in files:
        if 'extra_info_' not in L.raw(p):
            continue
        text = L.stripped(p)
        if p.startswith(MEMO_B_DIR) and p.endswith('.cpp'):
            for m in RX_B.finditer(text):
                fn = L.site_owner(p, m.start())
                tgt = re.search(r'static_cast<\s*([\w:]+)', m.group(0)).group(1)
                b_keys.add((p, L.line_of(p, m.start())))
                add(p, m.start(), '%s#%s' % (fn, tgt), 'non-const cast of ObExpr::extra_info_', 'memo:31')
        for m in RX_C.finditer(text):
            if (p, L.line_of(p, m.start())) in b_keys:
                continue
            fn = L.site_owner(p, m.start())
            tgt = m.group(1) or m.group(3)
            base = (m.group(2) if m.group(1) else m.group(4)) or ''
            pre = 'broadened;base:%s' % re.sub(r'\s+', '', base)
            hint = cg_hint(p, fn)
            if hint:
                pre += ';' + hint
            add(p, m.start(), '%s#%s' % (fn, tgt), 'non-const cast of ObExpr::extra_info_', pre)
    # --- D / E: const_cast to plan types
    d_keys = set()
    for p in files:
        if 'const_cast' not in L.raw(p):
            continue
        text = L.stripped(p)
        if p.startswith(MEMO_D_DIRS) and p.endswith(('.cpp', '.h')):
            for m in RX_D.finditer(text):
                fn = L.site_owner(p, m.start())
                tgt = re.search(r'(Ob\w*Spec)', m.group(0)).group(1)
                d_keys.add((p, m.start()))
                add(p, m.start(), '%s#const_cast<%s>' % (fn, tgt), 'const_cast to plan type', 'memo:6')
        for m in RX_E.finditer(text):
            if (p, m.start()) in d_keys:
                continue
            fn = L.site_owner(p, m.start())
            tgt = m.group(1).split('::')[-1]
            pre = 'broadened'
            hint = cg_hint(p, fn)
            if hint:
                pre += ';' + hint
            add(p, m.start(), '%s#const_cast<%s>' % (fn, tgt), 'const_cast to plan type', pre)
    # --- F: mutable members in engine types
    for p in files:
        if not p.startswith(F_DIRS) or 'mutable' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_F.finditer(text):
            off = m.start(2)
            cls = L.enclosing_class(p, off)
            if cls is None:
                continue
            add(p, off, '%s::%s' % (cls, m.group(2)), 'mutable member of engine type',
                'type:%s' % ' '.join(m.group(1).split()))
    n = L.finish(CAT, rows, 'A const-ObExpr& writes + B memo static_cast extra_info_ (31) + C broadened '
                            'extra_info_ casts + D memo const_cast<*Spec> (6) + E broadened const_cast to plan types '
                            '+ F mutable engine members')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-50s %d' % (k, v))
    pre = collections.Counter(r[4].split(';')[0] for r in rows)
    print('  pretags:', dict(sorted(pre.items())))
    return n


if __name__ == '__main__':
    main()
