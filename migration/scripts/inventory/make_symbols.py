#!/usr/bin/env python3
"""make_symbols.py: migration/design/symbols.tsv, name collisions under the planned glob re-exports
(RULEBOOK §4 "模块树和导入": every crate's lib.rs does `pub use <module>::*;` for every unit module, so two
units of one crate exporting one name collide; references then use the full path given here; §5.1).

Units: migration/design/units.tsv rows with disposition `translate` or `replace:*`, unit -> crate.
Top-level names per unit (astdb, generated files excluded; one entity per USR, located at its definition,
else its canonical declaration):
  class/struct/union/class_template definitions and named enums at namespace scope (explicit and
  partial specializations excluded: they implement the template), typedef/alias declarations at namespace
  scope (each unit that declares one counts: a typedef repeated in two headers is two Rust items),
  free functions and function templates (operators and explicit specializations excluded; a function
  renamed by overloads.tsv counts under its Rust name), namespace-scope variables and constants, and the
  constants of unscoped namespace-scope enums (RULEBOOK §2.12 emits them as module-level constants).
Rust namespaces: records, typedefs, aliases and enums are types; functions, variables, enum constants
and enums (a tuple struct also defines its constructor) are values.  A collision = one name exported by
two or more units of one crate in the same namespace; every unit's declaration is a row.
Columns (one file for both tables of the task): `crate name unit kind full_path header line rename`
  symbol rows: kind = C++ kind, full_path = `<crate>::<module path of the unit's target>::<name>`,
               header/line = the declaration, rename empty (RULEBOOK §4: refer by the full path)
  macro rows:  kind = `macro`; function-like macros (`#define NAME(`) defined in headers (.h .hpp .ipp .inc)
               of two or more units of one crate (compared by Rust name, i.e. lower case); the first
               definition (units.tsv dependency order, then file, then line) keeps the name, the others get
               the unit name as prefix: rename = `<unit file stem>_<macro>` in lower case (RULEBOOK §4:
               "其余加单元名前缀"); full_path = `<crate>::<rename>!` (#[macro_export] puts macros at the crate root)
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402
import make_overloads as MO  # noqa: E402

L = S.L
OUT = os.path.join(S.DESIGN, 'symbols.tsv')
HEADER = ['crate', 'name', 'unit', 'kind', 'full_path', 'header', 'line', 'rename']
TYPE_KINDS = ('class', 'struct', 'union', 'class_template', 'typedef', 'alias', 'enum')
VALUE_KINDS = ('function', 'function_template', 'var', 'enum_constant', 'enum')
RX_OPERATOR = re.compile(r'^operator(?![A-Za-z0-9_])')
HEADER_EXT = ('.h', '.hpp', '.ipp', '.inc')


def eligible_unit(un):
    return un is not None and (un.disposition == 'translate' or un.disposition.startswith('replace'))


def main():
    db = S.load_db(astdb.RECORD_KINDS + astdb.FUNC_KINDS + ('enum', 'enum_constant', 'typedef', 'alias', 'var'))
    _sets, _rows, conflicts, assigned = MO.compute(db)
    if conflicts:
        sys.exit('make_symbols: make_overloads reports conflicts')

    def is_record(u):
        return bool(u) and db.definition(u, astdb.RECORD_KINDS) is not None

    def ns_scope(r):
        """True if r is declared at namespace scope (its semantic parent is not a record or an enum)."""
        pu = r.get('parent_usr') or ''
        if is_record(pu):
            return False
        if pu and db.get(pu, ('enum',)):
            return False
        return True

    ents = []      # (name, kind, file, line, usr)
    seen = set()
    for r in db.records:
        k = r['kind']
        if r.get('generated'):
            continue
        if k in ('class', 'struct', 'union', 'class_template'):
            if not r.get('is_definition') or r.get('is_anonymous') or not r.get('name') or r.get('specialization_of') \
                    or r.get('explicit_instantiation') or '<' in r['qname'].split('::')[-1] or r['usr'] in seen:
                continue
            if not ns_scope(r):
                continue
            seen.add(r['usr'])
            ents.append((r['name'], k, r['file'], r['line'], r['usr']))
        elif k == 'enum':
            if not r.get('is_definition') or not r.get('name') or r['usr'] in seen or not ns_scope(r):
                continue
            seen.add(r['usr'])
            ents.append((r['name'], 'enum', r['file'], r['line'], r['usr']))
        elif k == 'enum_constant':
            if r['usr'] in seen:
                continue
            e = db.definition(r.get('parent_usr'), ('enum',)) if r.get('parent_usr') else None
            if e is None or e.get('is_scoped') or not ns_scope(e):
                continue
            seen.add(r['usr'])
            ents.append((r['name'], 'enum_constant', e['file'], r['line'], r['usr']))
        elif k in ('typedef', 'alias'):
            if not r.get('name') or not ns_scope(r):
                continue
            key = (r['usr'], r['file'])
            if key in seen:
                continue
            seen.add(key)
            ents.append((r['name'], k, r['file'], r['line'], r['usr']))
        elif k in ('function', 'function_template'):
            if r['usr'] in seen:
                continue
            seen.add(r['usr'])
            c = db.canonical(r['usr'])
            if c is None or c.get('generated') or RX_OPERATOR.match(c['name']) or '<>(' in (c.get('sig') or ''):
                continue
            if c['kind'] == 'function_template' and c.get('template_kind') not in (None, 'function'):
                continue
            if not ns_scope(c) or c.get('is_deleted'):
                continue
            nm = assigned.get(c['usr'], (c['name'], ''))[0]
            ents.append((nm, c['kind'], c['file'], c['line'], c['usr']))
        elif k == 'var':
            if r.get('is_member') or r['usr'] in seen or not ns_scope(r):
                continue
            seen.add(r['usr'])
            d = db.definition(r['usr'], ('var',)) or r
            ents.append((r['name'], 'var', d['file'], d['line'], r['usr']))
    groups = collections.defaultdict(list)    # (crate, ns, name) -> [(unit, kind, file, line)]
    for nm, k, f, line, usr in ents:
        un = S.unit_of(f)
        if not eligible_unit(un):
            continue
        for ns in (('type',) if k in TYPE_KINDS and k != 'enum' else ('value',) if k != 'enum' else ('type', 'value')):
            groups[(un.crate, ns, nm)].append((un, k, f, line))
    rows = {}
    for (crate, ns, nm), items in groups.items():
        units = {it[0].unit for it in items}
        if len(units) < 2:
            continue
        for un, k, f, line in items:
            key = (crate, nm, un.unit, k, f, line)
            rows[key] = [crate, nm, un.unit, k, '%s::%s::%s' % (crate, un.module_path, nm), f, str(line), '']
    nsym = len(rows)
    # ---- function-like macros defined in headers of two or more units of one crate
    mrows = []
    defs = collections.defaultdict(list)       # (crate, lower name) -> [(unit order, file, line, name, unit)]
    for name, mds in S.macro_index().items():
        for md in mds:
            if not md.function_like or not md.file.endswith(HEADER_EXT):
                continue
            un = S.unit_of(md.file)
            if not eligible_unit(un):
                continue
            defs[(un.crate, name.lower())].append((un.order, md.file, md.line, name, un))
    nmacro_names = 0
    for (crate, low), items in sorted(defs.items()):
        units = collections.OrderedDict()
        for it in sorted(items):
            units.setdefault(it[4].unit, it)
        if len(units) < 2:
            continue
        nmacro_names += 1
        for i, (order, f, line, name, un) in enumerate(units.values()):
            rename = low if i == 0 else '%s_%s' % (un.stem.lower(), low)
            mrows.append([crate, name, un.unit, 'macro', '%s::%s!' % (crate, rename), f, str(line), rename])
    out = sorted(rows.values(), key=lambda r: (r[0], r[1], r[2], r[3], r[5], int(r[6])))
    out += sorted(mrows, key=lambda r: (r[0], r[1].lower(), r[1], r[5], int(r[6])))
    L.write_tsv(OUT, HEADER, out)
    names = len({(r[0], r[1]) for r in rows.values()})
    by_crate = collections.Counter(r[0] for r in rows.values())
    print('symbols.tsv: %d symbol rows (%d colliding names; by crate %s) + %d macro rows (%d macro names)' % (
        nsym, names, dict(sorted(by_crate.items())), len(mrows), nmacro_names))


if __name__ == '__main__':
    main()
