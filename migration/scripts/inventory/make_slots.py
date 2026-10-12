#!/usr/bin/env python3
"""make_slots.py: migration/design/slots.tsv, one row per typed slot (RULEBOOK 0.5, §4 "槽位名", §5.1).

Rows:
  1. every type T of `server_service<T>()` (src/share/rc/ob_server_runtime.h: a process-wide pointer slot per
     type) in comment/string-stripped first-party sources, plus every T bound with `bind_server_service<T>`
     (also through a #define whose parameter is the type, e.g. `BIND_SERVICE(getter, type)` in
     ob_server_runtime_controller.cpp: the argument of each invocation).  `server_obj_pool<T>()` is
     `server_service<ObServerObjectPool<T>>()` and is counted for ObServerObjectPool<T>.  Arguments that are
     template parameters of generic code (`T`) are not types and are skipped.
     cpp_type = the record's qualified name (`oceanbase::` dropped) with its template arguments; file/line =
     the record's definition; crate = units.tsv crate of that file; uses = `server_service=<calls>` and,
     when bound, `bind=<n>`.
  2. every pointer member of `share::ObGlobalContext` (GCTX, src/share/ob_server_struct.h).  cpp_type = the
     pointee type; file/line = the member declaration; crate = the pointee type's crate (share for scalars);
     uses = `GCTX.<member>=<n>` (`GCTX.member` occurrences).
slot_static: RULEBOOK §4 — UPPER_SNAKE of the type name without the `Ob` prefix + `_SLOT`
(ObMultiVersionSchemaService -> MULTI_VERSION_SCHEMA_SERVICE_SLOT; template arguments appended the same
way).  GCTX members use the member name instead (`sql_proxy_` -> SQL_PROXY_SLOT) because two members share
a type (`sql_proxy_`, `ddl_sql_proxy_`: ObMySQLProxy *).  A name produced twice gets the enclosing namespace
prepended (STORAGE_…); the script exits non-zero if names still collide.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
OUT = os.path.join(S.DESIGN, 'slots.tsv')
HEADER = ['cpp_type', 'slot_static', 'crate', 'file', 'line', 'uses']
GCTX_QNAME = 'oceanbase::share::ObGlobalContext'
RX_CALL = re.compile(r'\b(server_service|bind_server_service|unbind_server_service|server_obj_pool)\s*<')


def template_arg(text, i):
    """Balanced template argument starting at text[i] (just after '<'); (arg, index after '>')."""
    d, j = 1, i
    while j < len(text) and d:
        ch = text[j]
        if ch == '<':
            d += 1
        elif ch == '>':
            d -= 1
        elif ch in ';{}':
            return None, j
        j += 1
    return ' '.join(text[i:j - 1].split()), j


def split_top(s, sep=','):
    out, d, cur = [], 0, []
    for ch in s:
        if ch in '<([':
            d += 1
        elif ch in '>)]':
            d -= 1
        if ch == sep and d == 0:
            out.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if ''.join(cur).strip():
        out.append(''.join(cur).strip())
    return out


class TypeNamer(object):
    def __init__(self, db, env):
        self.db, self.env = db, env

    def resolve(self, spelled):
        """(record usr, normalized cpp_type, [template argument names]) or None."""
        s = spelled.strip().lstrip(':').strip()
        s = re.sub(r'^(?:const\s+|typename\s+)', '', s)
        m = re.match(r'^([^<]+?)\s*(?:<(.*)>)?$', s)
        if not m:
            return None
        base, targs = m.group(1).strip(), m.group(2)
        ru = self.env.record_of_type(base)
        if ru is None:
            return None
        d = self.db.definition(ru, astdb.RECORD_KINDS)
        name = S.short_qname(d['qname'])
        argnames = []
        if targs:
            parts = []
            for a in split_top(targs):
                ra = self.resolve(a)
                if ra is not None:
                    parts.append(ra[1])
                    argnames.append(self.db.definition(ra[0], astdb.RECORD_KINDS)['name'])
                else:
                    parts.append(' '.join(a.split()))
                    argnames.append(re.sub(r'\W+', '_', a.split('::')[-1]).strip('_'))
            name = '%s<%s>' % (name, ', '.join(parts))
        return ru, name, argnames


def main():
    db = S.load_db(astdb.RECORD_KINDS + ('field',))
    env = S.TypeEnv(db)
    tn = TypeNamer(db, env)
    midx = S.macro_index()
    macro_lines = {}
    for nm, mds in midx.items():
        for md in mds:
            for k in range(md.nlines):
                macro_lines[(md.file, md.line + k)] = md
    uses = collections.Counter()
    binds = collections.Counter()
    info = {}
    skipped = collections.Counter()

    def add(kind, spelled, where):
        r = tn.resolve(spelled)
        if r is None:
            skipped[spelled] += 1
            return
        ru, name, argnames = r
        info.setdefault(name, (ru, argnames))
        if kind == 'use':
            uses[name] += 1
        else:
            binds[name] += 1

    for p in S.first_party_code():
        text = L.stripped(p)
        if 'server_' not in text:
            continue
        for m in RX_CALL.finditer(text):
            arg, end = template_arg(text, m.end())
            if arg is None:
                continue
            fn = m.group(1)
            line = L.line_of(p, m.start())
            md = macro_lines.get((p, line))
            if fn == 'unbind_server_service':
                continue
            if md is not None and md.params and arg in md.params:
                # the type is a macro parameter: take the argument of every invocation of that macro
                k = md.params.index(arg)
                rx_inv = re.compile(r'\b%s\s*\(' % re.escape(md.name))
                for q in S.first_party_code():
                    tq = L.stripped(q)
                    if md.name not in tq:
                        continue
                    for mi in rx_inv.finditer(tq):
                        if macro_lines.get((q, L.line_of(q, mi.start()))) is not None:
                            continue
                        d, j = 1, mi.end()
                        while j < len(tq) and d:
                            if tq[j] == '(':
                                d += 1
                            elif tq[j] == ')':
                                d -= 1
                            j += 1
                        args = split_top(tq[mi.end():j - 1])
                        if k < len(args):
                            add('use' if fn in ('server_service', 'server_obj_pool') else 'bind',
                                args[k] if fn != 'server_obj_pool' else 'common::ObServerObjectPool<%s>' % args[k],
                                '%s:%d' % (q, L.line_of(q, mi.start())))
                continue
            if fn == 'server_obj_pool':
                arg = 'common::ObServerObjectPool<%s>' % arg
            if fn in ('server_service', 'server_obj_pool'):
                # the template's own body (`server_service<Service>`) and generic helpers are not uses
                if re.match(r'^(?:T|U|Service|ServiceType|Type)$', arg) or arg.endswith('<T>'):
                    skipped[arg] += 1
                    continue
                add('use', arg, '%s:%d' % (p, line))
            elif fn == 'bind_server_service':
                if re.match(r'^(?:T|U|Service|ServiceType|Type)$', arg):
                    skipped[arg] += 1
                    continue
                add('bind', arg, '%s:%d' % (p, line))
    rows = []
    for name, (ru, argnames) in info.items():
        d = db.definition(ru, astdb.RECORD_KINDS)
        slot = S.upper_snake(S.strip_ob(d['name']))
        if argnames:
            slot += '_' + '_'.join(S.upper_snake(S.strip_ob(a)) for a in argnames if a)
        un = S.unit_of(d['file'])
        u = 'server_service=%d' % uses[name]
        if binds[name]:
            u += ';bind=%d' % binds[name]
        rows.append({'cpp_type': name, 'slot': slot + '_SLOT', 'crate': un.crate if un else '?', 'file': d['file'],
                     'line': d['line'], 'uses': u, 'qname': d['qname']})
    # GCTX pointer members
    gctx = [r for r in db.find(GCTX_QNAME, astdb.RECORD_KINDS) if r.get('is_definition')]
    gcount = collections.Counter()
    if gctx:
        members = [f for f in db.fields(gctx[0]['usr']) if (f.get('canonical_kind') == 'pointer' or
                                                            (f.get('canonical_type') or '').rstrip().endswith('*'))]
        names = {f['name'] for f in members}
        rx_g = re.compile(r'\bGCTX\s*\.\s*(%s)\b' % '|'.join(re.escape(n) for n in sorted(names)))
        for p in S.first_party_code():
            t = L.stripped(p)
            if 'GCTX' in t:
                for m in rx_g.finditer(t):
                    gcount[m.group(1)] += 1
        for f in members:
            pt = re.sub(r'\*\s*$', '', S.L._clean(f.get('canonical_type') or '')).strip()
            pt = re.sub(r'^const\s+', '', pt)
            r = tn.resolve(pt)
            if r is not None:
                pd = db.definition(r[0], astdb.RECORD_KINDS)
                un = S.unit_of(pd['file'])
                crate = un.crate if un else '?'
                ctype = r[1]
            else:
                un = S.unit_of(f['file'])
                crate = un.crate if un else '?'
                ctype = re.sub(r'\*\s*$', '', S.L._clean(f.get('type') or pt)).strip()
            rows.append({'cpp_type': ctype, 'slot': S.upper_snake(f['name'].rstrip('_')) + '_SLOT', 'crate': crate,
                         'file': f['file'], 'line': f['line'], 'uses': 'GCTX.%s=%d' % (f['name'], gcount[f['name']]),
                         'qname': 'GCTX.' + f['name']})
    # disambiguate repeated slot names by the enclosing namespace
    by_slot = collections.defaultdict(list)
    for r in rows:
        by_slot[r['slot']].append(r)
    for slot, rs in by_slot.items():
        if len(rs) > 1:
            for r in rs:
                if r['qname'].startswith('GCTX.'):
                    continue
                parts = r['qname'].split('::')
                ns = parts[-2] if len(parts) > 1 else ''
                if ns and ns != 'oceanbase':
                    r['slot'] = S.upper_snake(ns) + '_' + r['slot']
    final = collections.Counter(r['slot'] for r in rows)
    dups = [s for s, n in final.items() if n > 1]
    if dups:
        sys.exit('make_slots: slot name collisions: %s' % ', '.join(sorted(dups)))
    out = sorted(([r['cpp_type'], r['slot'], r['crate'], r['file'], str(r['line']), r['uses']] for r in rows),
                 key=lambda x: (x[5].startswith('GCTX'), x[0], x[1]))
    L.write_tsv(OUT, HEADER, out)
    ns = sum(1 for r in rows if not r['qname'].startswith('GCTX.'))
    print('slots.tsv: %d rows (%d server_service types, %d GCTX pointer members); server_service calls %d; '
          'skipped generic arguments %s' % (len(out), ns, len(out) - ns, sum(uses.values()), dict(skipped)))


if __name__ == '__main__':
    main()
