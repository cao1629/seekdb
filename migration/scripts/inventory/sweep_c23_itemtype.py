#!/usr/bin/env python3
"""c23-itemtype: the two ObItemType definitions (RULEBOOK 0.13 ObItemType) and the compilation units in
which the objit copy wins (both copies use the include guard OCEANBASE_SQL_OB_ITEM_TYPE_H_, so the copy
a translation unit includes first is the one it sees; src/sql/parser/ob_item_type.h only forwards to the
query copy).

  A  enumerator diff: every enum of src/objit/include/objit/common/ob_item_type.h and
     src/query/api/query/parser/ob_item_type.h is parsed and evaluated (implicit values = previous + 1);
     one row per enumerator missing from one copy or with a different value; an enum defined in only one
     copy is compared with the definition the other copy includes (e.g. share/cache/ob_cache_type.h)
  B  compilation units: every command of build_release/compile_commands.json is re-run as
     `<compiler> <same flags> -E -H -Wno-error -o /dev/null` (preprocessing only, run in the build
     directory, nothing written; 8 in parallel); the first ob_item_type.h copy in the -H include trace is
     the effective one. All TUs go to migration/inventory/sweep/c23-itemtype-tu.tsv; each TU where the
     objit copy wins becomes a sweep row at the #include line that pulled the objit copy in.
"""
import ast
import collections
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c23-itemtype'
OBJIT = 'src/objit/include/objit/common/ob_item_type.h'
QUERY = 'src/query/api/query/parser/ob_item_type.h'
SHIM = 'src/sql/parser/ob_item_type.h'
TU_OUT = os.path.join(L.SWEEP_DIR, 'c23-itemtype-tu.tsv')
BUILD = os.path.join(L.REPO, 'build_release')
RX_ENUM = re.compile(r'\benum\s+(?:class\s+|struct\s+)?(\w+)\s*(?::\s*[\w:\s]+)?\{([^{}]*)\}', re.S)


def safe_eval(expr, env):
    expr = expr.strip()
    expr = re.sub(r'\b([A-Za-z_]\w*)\b', lambda m: str(env[m.group(1)]) if m.group(1) in env else m.group(0), expr)
    expr = re.sub(r'(?<=\d)[uUlL]+\b', '', expr)
    tree = ast.parse(expr, mode='eval')
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult,
                              ast.LShift, ast.RShift, ast.BitOr, ast.BitAnd, ast.USub, ast.UAdd, ast.FloorDiv)):
            raise ValueError('cannot evaluate %r' % expr)
    return eval(compile(tree, '<enum>', 'eval'), {'__builtins__': {}})


def parse_enums(path):
    """{enum: OrderedDict(name -> (value, line))} for every enum in path."""
    text = L.stripped(path)
    out = collections.OrderedDict()
    for m in RX_ENUM.finditer(text):
        ename = m.group(1)
        body_off = m.start(2)
        env = {}
        vals = collections.OrderedDict()
        nxt = 0
        for em in re.finditer(r'([A-Za-z_]\w*)\s*(?:=\s*([^,]+?))?\s*(?:,|$)', m.group(2)):
            name = em.group(1)
            if em.group(2) is not None:
                v = safe_eval(em.group(2), env)
            else:
                v = nxt
            env[name] = v
            nxt = v + 1
            vals[name] = (v, L.line_of(path, body_off + em.start(1)))
        out[ename] = vals
    return out


def includes_of(path):
    res = []
    for m in re.finditer(r'(?m)^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', L.raw(path)):
        for cand in ('src/' + m.group(1), 'src/oblib/' + m.group(1)):
            if os.path.exists(os.path.join(L.REPO, cand)):
                res.append(cand)
                break
    return res


def enum_rows():
    rows = []
    eo, eq = parse_enums(OBJIT), parse_enums(QUERY)
    for ename in sorted(set(eo) | set(eq)):
        a, b = eo.get(ename), eq.get(ename)
        pa, pb = OBJIT, QUERY
        note = ''
        if a is None or b is None:
            # compare with the definition the other copy pulls in through its includes
            other = OBJIT if a is None else QUERY
            for inc in includes_of(other):
                e2 = parse_enums(inc)
                if ename in e2:
                    if a is None:
                        a, pa = e2[ename], inc
                    else:
                        b, pb = e2[ename], inc
                    note = 'enum defined in %s only; the other copy includes %s' % (
                        OBJIT if eo.get(ename) is not None else QUERY, inc)
                    break
            if a is None or b is None:
                only = OBJIT if a is not None else QUERY
                vals = a if a is not None else b
                for name, (v, ln) in vals.items():
                    rows.append((only, '%s::%s' % (ename, name), 'enumerator of an enum defined in one copy only', ln,
                                 'value=%d' % v))
                continue
        for name in list(a.keys()) + [n for n in b.keys() if n not in a]:
            va, vb = a.get(name), b.get(name)
            if va and vb and va[0] == vb[0]:
                continue
            if va and not vb:
                rows.append((pa, '%s::%s' % (ename, name), 'enumerator only in %s' % pa, va[1],
                             'value=%d%s' % (va[0], ';' + note if note else '')))
            elif vb and not va:
                rows.append((pb, '%s::%s' % (ename, name), 'enumerator only in %s' % pb, vb[1],
                             'value=%d%s' % (vb[0], ';' + note if note else '')))
            else:
                rows.append((pa, '%s::%s' % (ename, name), 'enumerator value differs between the copies', va[1],
                             'objit=%d;query=%d;query_line=%s:%d' % (va[0], vb[0], pb, vb[1])))
    return rows


def preprocess(entry):
    args = shlex.split(entry['command'])
    out, skip = [], False
    for x in args:
        if skip:
            skip = False
            continue
        if x == '-o':
            skip = True
            continue
        if x == '-c':
            continue
        out.append(x)
    cmd = out[:1] + ['-E', '-H', '-Wno-error', '-o', '/dev/null'] + out[1:]
    p = subprocess.run(cmd, cwd=entry['directory'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    trace = []
    for ln in p.stderr.decode('utf-8', 'replace').splitlines():
        m = re.match(r'^(\.+) (.+)$', ln)
        if m:
            trace.append((len(m.group(1)), m.group(2)))
    return p.returncode, trace


def norm(path, cwd):
    ap = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    return L.rel(ap) if ap.startswith(L.REPO + os.sep) else ap


def tu_rows():
    with open(os.path.join(BUILD, 'compile_commands.json'), encoding='utf-8') as fh:
        cc = json.load(fh)
    cc.sort(key=lambda e: e['file'])
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(preprocess, cc))
    table, sweep = [], []
    fp_set = set(L.first_party())
    risky = re.compile(r'(?<![\w:])(?:ObItemType\s*::\s*)?(T_MAX|T_AUTO_PARTITION|T_LS_ATTR_LIST|T_ALTER_LS|T_FLUSH_PRIVILEGES|T_RESERVED_3791)\b')
    other_enums = {'src/share/statement/ob_stmt_type.h'}  # defines stmt::T_MAX, stmt::T_FLUSH_PRIVILEGES
    for e, (rc, trace) in zip(cc, results):
        tu = norm(e['file'], e['directory'])
        stack = {}
        eff, chain, seen = 'none', [], set()
        for depth, path in trace:
            rp = norm(path, e['directory'])
            stack[depth] = rp
            for k in [k for k in stack if k > depth]:
                del stack[k]
            if rp in (OBJIT, QUERY):
                seen.add('objit' if rp == OBJIT else 'query')
                if eff == 'none':
                    eff = 'objit' if rp == OBJIT else 'query'
                    chain = [stack[k] for k in sorted(stack)]
        first_member = chain[0] if chain else ''
        table.append((tu, eff, first_member, ' -> '.join(chain), 'yes' if 'objit' in seen else 'no',
                      'yes' if 'query' in seen else 'no', str(rc)))
        if eff == 'objit':
            parent = chain[-2] if len(chain) >= 2 else tu
            line = 1
            if os.path.exists(os.path.join(L.REPO, parent)):
                for i, ln in enumerate(L.raw_lines(parent)):
                    if re.match(r'\s*#\s*include\s*["<]objit/common/ob_item_type\.h[">]', ln):
                        line = i + 1
                        break
            closure = {norm(p, e['directory']) for _d, p in trace} | {tu}
            closure = sorted(f for f in closure if f in fp_set and f not in (OBJIT, QUERY, SHIM) and f not in other_enums)
            uses = sorted({u for f in closure if 'T_' in L.raw(f) for u in risky.findall(L.stripped(f))})
            sweep.append((parent, 'tu:%s' % tu, 'compilation unit where the objit ObItemType copy is effective', line,
                          'first-member:%s;uses:%s' % (first_member, ','.join(uses) or '-')))
    return table, sweep


def main():
    rows = enum_rows()
    table, sweep = tu_rows()
    L.write_tsv(TU_OUT, ['tu', 'effective_copy', 'first_member', 'include_chain', 'objit_seen', 'query_seen',
                         'preprocess_rc'], table)
    rows += sweep
    n = L.finish(CAT, rows, 'A enumerator differences between the objit and query copies + B compilation units in '
                            'which the objit copy is included first (clang -E -H over compile_commands.json); '
                            'all TUs in c23-itemtype-tu.tsv')
    eff = collections.Counter(t[1] for t in table)
    bad = sum(1 for t in table if t[6] != '0')
    print('  TUs: %d %s; preprocess failures: %d -> %s' % (len(table), dict(sorted(eff.items())), bad, L.rel(TU_OUT)))
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-70s %d' % (k, v))
    return n


if __name__ == '__main__':
    main()
