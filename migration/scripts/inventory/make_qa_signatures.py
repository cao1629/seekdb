#!/usr/bin/env python3
"""make_qa_signatures.py: migration/design/qa_signatures.tsv (RULEBOOK §2.6 R1, 0.6, §5.1).

R1: a function whose signature (parameters, return type, or owning class) mentions a compile-time IR type
takes `qa: &mut QueryArena` as its first parameter after self.  The IR type set is derived from the
database and written to stdout with one justification per type (the README section repeats the rules):
  N  node types: oceanbase::sql::ObRawExpr, ObStmt (amendment 07-A3: ObStmt, not only ObDMLStmt; the task
     names both), TableItem, ObLogicalOperator, Path, and every record deriving from them (astdb derived)
  F  factories: records named *Factory with a method that creates a node type or a logical plan
     (returns a pointer to an N type or ObLogPlan, or has an output parameter `X *&`/`X **` of such a type
     or of a template parameter in a method named create*/alloc*): ObRawExprFactory, ObStmtFactory,
     ObLogOperatorFactory, ObLogPlanFactory
  C  IR context types: records with a data member whose type mentions an F type (by value, pointer,
     reference or template argument), and the records deriving from them; justification = that member.
     Excluded: ObExecContext (runtime execution context, RULEBOOK 0.6 执行期: its stmt_factory_ serves
     PL/dynamic SQL; the functions that compile through it carry IR types in their own signatures).
     Holders of a context type (ObTransformRule::ctx_, ObResolver::params_, ObJoinOrder::plan_, …) are not
     context types themselves; their methods are listed only when their signatures mention an IR type.
Rows: every function/method/function template/constructor (canonical declaration, generated files
excluded) whose parameter or return canonical types mention an N/F/C type, or whose owning class is one;
destructors, conversion and operator functions are not listed (Drop and operator traits cannot take qa).
Columns: function (`<Class>::<sig>`), file, line (canonical declaration), kind (method / static method /
function / function template / method template / constructor), ir_types (`param:` / `return:` / `owner:`
lists of matched type names, the owner tagged node/factory/context), is_ir_node_method (1 when the owning
class is an N type: amendment 07-A3 may turn those into associated functions or node accessors),
qa_param (`qa: &mut QueryArena`, R1: first parameter after self, first parameter of free/static functions
and constructors).
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
OUT = os.path.join(S.DESIGN, 'qa_signatures.tsv')
HEADER = ['function', 'file', 'line', 'kind', 'ir_types', 'is_ir_node_method', 'qa_param']
ROOTS = ['oceanbase::sql::ObRawExpr', 'oceanbase::sql::ObStmt', 'oceanbase::sql::TableItem',
         'oceanbase::sql::ObLogicalOperator', 'oceanbase::sql::Path']
EXCLUDED_CONTEXT = {'oceanbase::sql::ObExecContext': 'runtime execution context (RULEBOOK 0.6 执行期)'}
RX_OPERATOR = re.compile(r'^operator(?![A-Za-z0-9_])')


def ir_sets(db):
    def defn(u):
        return db.definition(u, astdb.RECORD_KINDS)

    node = collections.OrderedDict()
    for q in ROOTS:
        for r in db.find(q, astdb.RECORD_KINDS):
            if r.get('is_definition'):
                node[r['usr']] = 'node: %s' % q.split('::')[-1]
                for d in sorted(db.derived(r['usr'])):
                    node.setdefault(d, 'node: derives from %s' % q.split('::')[-1])
    node_q = {defn(u)['qname'] for u in node if defn(u)}
    plan_q = 'oceanbase::sql::ObLogPlan'
    produce_q = sorted(node_q | {plan_q})
    rx_prod = re.compile(r'\b(%s)\b' % '|'.join(re.escape(q) for q in produce_q))
    alt = '|'.join(re.escape(q) for q in produce_q)
    rx_ret = re.compile(r'^(?:const\s+)?(?:%s)\s*\*$' % alt)
    rx_out = re.compile(r'^(?:const\s+)?(?:%s)\s*\*\s*(?:&|\*)$' % alt)
    rx_in = re.compile(r'^(?:const\s+)?(?:%s)\s*\*$' % alt)
    fac = collections.OrderedDict()
    for r in db.by_kind(*astdb.RECORD_KINDS):
        if not r.get('is_definition') or r.get('generated') or not r['name'].endswith('Factory'):
            continue
        for m in db.methods(r['usr']):
            rt = S.L._clean(m.get('canonical_return_type') or '')
            why = None
            if rx_ret.match(rt):
                why = '%s returns %s' % (m['name'], rt.replace('oceanbase::', ''))
            else:
                for p in m.get('params', []) or []:
                    t = S.L._clean(p.get('canonical_type') or '')
                    if rx_out.match(t):
                        why = '%s outputs %s' % (m['name'], S.L._clean(p.get('type') or t))
                        break
                    if rx_in.match(t) and re.match(r'(?:free|destroy|release|recycle)', m['name']):
                        why = '%s takes back %s' % (m['name'], S.L._clean(p.get('type') or t))
                        break
            if why:
                fac[r['usr']] = 'factory: %s (%s:%d)' % (why, m['file'], m['line'])
                break
    fac_q = sorted(defn(u)['qname'] for u in fac)
    rx_fac = re.compile(r'\b(%s)\b' % '|'.join(re.escape(q) for q in fac_q)) if fac_q else None
    ctx = collections.OrderedDict()
    excluded = collections.OrderedDict()
    for f in sorted(db.by_kind('field'), key=lambda f: (f['file'], f['line'], f['usr'])):
        if f.get('generated') or rx_fac is None:
            continue
        m = rx_fac.search(f.get('canonical_type') or '')
        if not m:
            continue
        o = defn(f['parent_usr']) if f.get('parent_usr') else None
        while o is not None and o.get('is_anonymous') and o.get('parent_usr'):
            o = defn(o['parent_usr'])
        if o is None or o['usr'] in node or o['usr'] in fac or o['usr'] in ctx:
            continue
        why = 'context: holds %s %s (%s:%d)' % (f['name'], S.L._clean(f.get('type') or ''), f['file'], f['line'])
        if o['qname'] in EXCLUDED_CONTEXT:
            excluded[o['usr']] = why + '; excluded: ' + EXCLUDED_CONTEXT[o['qname']]
            continue
        ctx[o['usr']] = why
    for u in list(ctx):
        for d in sorted(db.derived(u)):
            if d not in ctx and d not in node and d not in fac and d not in excluded:
                ctx[d] = 'context: derives from %s' % defn(u)['qname'].replace('oceanbase::', '')
    return node, fac, ctx, excluded


def main():
    db = S.load_db(astdb.RECORD_KINDS + ('field',) + astdb.FUNC_KINDS)
    node, fac, ctx, excluded = ir_sets(db)
    role = {}
    for u in node:
        role[u] = 'node'
    for u in fac:
        role[u] = 'factory'
    for u in ctx:
        role[u] = 'context'
    qn = {u: db.definition(u, astdb.RECORD_KINDS)['qname'] for u in role}
    names = sorted(set(qn.values()), key=lambda q: (-len(q), q))
    rx = re.compile(r'\b(%s)\b(?!\s*::)' % '|'.join(re.escape(q) for q in names))
    short = {q: q.replace('oceanbase::', '') for q in names}
    rows = []
    seen = set()
    for r in db.records:
        if r['kind'] not in ('function', 'method', 'function_template', 'ctor') or r['usr'] in seen:
            continue
        seen.add(r['usr'])
        c = db.canonical(r['usr'])
        if c is None or c.get('generated') or RX_OPERATOR.match(c['name']) or c['name'].startswith('~'):
            continue
        if c['kind'] == 'function_template' and c.get('template_kind') in ('dtor', 'conversion'):
            continue
        pm = sorted({short[m.group(1)] for p in c.get('params', []) or []
                     for m in rx.finditer(p.get('canonical_type') or '')})
        rm = sorted({short[m.group(1)] for m in rx.finditer(c.get('canonical_return_type') or '')})
        pu = c.get('parent_usr')
        owner_role = role.get(pu) if pu else None
        if not pm and not rm and not owner_role:
            continue
        parts = []
        if pm:
            parts.append('param:%s' % ','.join(pm))
        if rm:
            parts.append('return:%s' % ','.join(rm))
        if owner_role:
            parts.append('owner:%s(%s)' % (short[qn[pu]], owner_role))
        tk = c.get('template_kind')
        if c['kind'] == 'ctor' or tk == 'ctor':
            kind = 'constructor'
        elif c['kind'] == 'method':
            kind = 'static method' if c.get('is_static') else 'method'
        elif c['kind'] == 'function_template':
            kind = 'method template' if tk == 'method' else 'function template'
        else:
            kind = 'function'
        sig = c.get('sig') or c['name']
        if c.get('is_const'):
            sig += ' const'
        scope = S.short_qname(c.get('parent_qname') or '')
        fn = '%s::%s' % (scope, sig) if scope else sig
        rows.append([fn, c['file'], str(c['line']), kind, '; '.join(parts), '1' if owner_role == 'node' else '0',
                     'qa: &mut QueryArena'])
    rows.sort(key=lambda r: (r[1], int(r[2]), r[0]))
    seen_fn = collections.Counter()
    for r in rows:
        k = (r[0], r[1], r[2])
        seen_fn[k] += 1
        if seen_fn[k] > 1:
            r[0] = '%s#%d' % (r[0], seen_fn[k])
    L.write_tsv(OUT, HEADER, rows)
    print('qa_signatures.tsv: %d rows (node methods %d); IR types: node %d, factory %d, context %d, excluded %d' % (
        len(rows), sum(1 for r in rows if r[5] == '1'), len(node), len(fac), len(ctx), len(excluded)))
    for title, d in (('factory', fac), ('context', ctx), ('excluded', excluded)):
        for u, why in d.items():
            print('  %s\t%s\t%s' % (title, qn.get(u) or db.definition(u, astdb.RECORD_KINDS)['qname'], why))


if __name__ == '__main__':
    main()
