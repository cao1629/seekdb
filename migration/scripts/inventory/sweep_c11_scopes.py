#!/usr/bin/env python3
"""c11-scopes: temporary scopes and arena levels (RULEBOOK 0.6 临时上下文, 0.7, §2.4).

Patterns (comment/string-stripped first-party text; see README.md, section c11-scopes):
  A  `CREATE_WITH_TEMP_CONTEXT(` / `CREATE_WITH_TEMP_CONTEXT_P(` call sites (not #define lines)
  B  function-local factories `Ob(RawExpr|Stmt|LogOperator|LogPlan)Factory x(...)|x;|x{...}` inside a
     function body; pretag memo:33 marks the ir-graphs memo set
     (`grep -rnE 'ObRawExprFactory\\s+\\w+\\s*\\(' src/sql src/pl --include='*.cpp'`)
  C  arena-typed data members: a class-body declaration whose type name contains `Arena`
     (ObArenaAllocator, ObSafeArenaAllocator, ObSafeArena, PageArena<...>, ModuleArena, ObFifoArena,
     ObArenaObjPool<...>, typedef'd *Arena types); pointer/reference members are excluded (c04)
  D  every `OB_NEWx(T, pool, ...)` call site with its pool argument
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c11-scopes'
RX_TEMP_CTX = re.compile(r'\b(CREATE_WITH_TEMP_CONTEXT(?:_P)?)\s*\(')
RX_FACTORY = re.compile(r'\b((?:sql::)?Ob(RawExpr|Stmt|LogOperator|LogPlan)Factory)\s+(\w+)\s*(\(|;|\{)')
RX_ARENA_MEMBER = re.compile(
    r'(?m)^[ \t]*((?:static\s+|mutable\s+|alignas\s*\([^)]*\)\s+)*)((?:\w+::)*(\w*Arena\w*)(?:\s*<[^;{}()]*>)?)\s+'
    r'([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)\s*(?:\{[^;{}]*\}|\([^;{}]*\)|=[^;]*)?\s*;')
RX_OB_NEWX = re.compile(r'\bOB_NEWx\s*\(')
REQUEST_ENTRY_FILES = ('src/observer/omt/ob_th_worker.cpp', 'src/observer/mysql/obmp_query.cpp',
                       'src/observer/mysql/obmp_stmt_execute.cpp', 'src/oblib/rpc/frame/ob_req_queue_thread.cpp')
SQL_COMPILE_DIRS = ('src/sql/resolver/', 'src/sql/rewrite/', 'src/sql/optimizer/', 'src/sql/code_generator/',
                    'src/sql/printer/', 'src/sql/parser/', 'src/sql/hybrid_search/')
SQL_EXEC_DIRS = ('src/sql/engine/', 'src/sql/das/', 'src/sql/dtl/', 'src/sql/executor/', 'src/query/api/query/engine/')


def split_macro_args(text, open_paren):
    """Top-level comma-separated arguments of a macro call starting at text[open_paren] == '('."""
    depth, args, cur, i = 0, [], [], open_paren
    while i < len(text):
        c = text[i]
        if c in '([{':
            depth += 1
            if depth > 1:
                cur.append(c)
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                args.append(''.join(cur))
                return args, i
            cur.append(c)
        elif c == ',' and depth == 1:
            args.append(''.join(cur))
            cur = []
        else:
            if depth >= 1:
                cur.append(c)
        i += 1
    return args, i


def norm(s):
    return re.sub(r'\s+', '', s)


def arena_guess(path, member):
    if path.startswith(('src/oblib/lib/allocator/', 'src/oblib/lib/alloc/')):
        return 'infra-internal'
    if path.startswith(SQL_COMPILE_DIRS):
        return 'guess:ARENA_REQUEST'
    if path.startswith(SQL_EXEC_DIRS):
        if re.search(r'(tmp|batch|row|eval|iter|cur|local|calc)', member):
            return 'guess:ARENA_BATCH'
        return 'guess:ARENA_CROSSCALL'
    return 'guess:ARENA_CROSSCALL'


def pool_kind(pool):
    p = norm(pool)
    while p.startswith('(') and p.endswith(')') and split_macro_args(p, 0)[1] == len(p) - 1:
        p = p[1:-1]
    if not p:
        return 'pool-kind:empty'
    if p.startswith('&'):
        return 'pool-kind:addr-of'
    if '(' in p:
        return 'pool-kind:call'
    if re.search(r'(\.|->)', p):
        return 'pool-kind:field-of'
    if p.endswith('_'):
        return 'pool-kind:member'
    return 'pool-kind:param-or-local'


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
    temp_blocks = collections.defaultdict(list)  # path -> [(open, close)]
    # --- A
    for p in files:
        if 'CREATE_WITH_TEMP_CONTEXT' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_TEMP_CTX.finditer(text):
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if text[ls:m.start()].lstrip().startswith('#'):
                continue
            fn = L.site_owner(p, m.start())
            args, end = split_macro_args(text, m.end() - 1)
            block = next((s for s in L.scopes(p) if s.open > end), None)
            body = text[block.open:block.close] if block else ''
            if block:
                temp_blocks[p].append((block.open, block.close))
            if p in REQUEST_ENTRY_FILES:
                guess = 'guess:SCOPE_REQUEST'
            elif re.search(r'\bOb(RawExpr|Stmt)Factory\s+\w+', body):
                guess = 'guess:SCOPE_STMT_QA'
            else:
                guess = 'guess:SCOPE_LOCAL_ARENA'
            add(p, m.start(), '%s#%s' % (fn, m.group(1)), 'temporary memory context', guess)
    # --- B
    for p in files:
        if 'Factory' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_FACTORY.finditer(text):
            off = m.start(3)
            in_macro = L.macro_at(p, off)
            if not in_macro and not L.in_function(p, off):
                continue
            fn = L.site_owner(p, off)
            pre = []
            if p.startswith(('src/sql/', 'src/pl/')) and p.endswith('.cpp') and m.group(2) == 'RawExpr' and m.group(4) == '(':
                pre.append('memo:33')
            else:
                pre.append('broadened')
            if any(a <= off <= b for a, b in temp_blocks.get(p, ())):
                pre.append('inside-temp-context')
            add(p, off, '%s#%s:%s' % (fn, m.group(1).split('::')[-1], m.group(3)), 'local IR factory', ';'.join(pre))
    # --- C
    for p in files:
        if 'Arena' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_ARENA_MEMBER.finditer(text):
            tname = m.group(3)
            off = m.start(4)
            cls = L.enclosing_class(p, off)
            if cls is None:
                continue
            quals = m.group(1) or ''
            if 'ObjPool' in tname:
                cons = 'arena-backed pool member'
            elif 'static' in quals:
                cons = 'static arena member'
            else:
                cons = 'arena member'
            member = m.group(4) + (norm(m.group(5)) if m.group(5) else '')
            pre = '%s;type:%s' % (arena_guess(p, m.group(4)), norm(m.group(2)))
            add(p, off, '%s::%s' % (cls, member), cons, pre)
    # --- D
    for p in files:
        if 'OB_NEWx' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_OB_NEWX.finditer(text):
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if text[ls:m.start()].lstrip().startswith('#'):
                continue
            args, _ = split_macro_args(text, m.end() - 1)
            if len(args) < 2:
                continue
            t, pool = norm(args[0]), norm(args[1])
            fn = L.site_owner(p, m.start())
            add(p, m.start(), '%s#OB_NEWx(%s,%s)' % (fn, t, pool), 'OB_NEWx pool argument',
                '%s;pool:%s' % (pool_kind(pool), pool))
    n = L.finish(CAT, rows, 'A CREATE_WITH_TEMP_CONTEXT + B local IR factories (memo 33 tagged) + '
                            'C arena-typed members + D OB_NEWx pool arguments')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-40s %d' % (k, v))
    pre = collections.Counter(r[4].split(';')[0] for r in rows)
    print('  pretags:', dict(sorted(pre.items())))
    return n


if __name__ == '__main__':
    main()
