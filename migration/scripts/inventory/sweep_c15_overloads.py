#!/usr/bin/env python3
"""c15-overloads: one row per overload set with more than 20 members (RULEBOOK §4 "超过 20 个重载的集合…
清单第 15 类单列复核", §5.2 #15).

Membership is exactly make_overloads.py's (`Sets`): same parent scope + same name; constructors,
destructors, conversions, operators, explicit specializations (`name<>(…)`), deleted and generated
functions excluded; internal-linkage free functions grouped per file.  The Rust names of the members are
in migration/design/overloads.tsv (rows with this scope and name); agents review them.
Row: file/line of the first-declared member, symbol `<scope>::<name>`, construct
`overload set (more than 20 members)`, pretag `members=<n>; renamed=<members whose Rust name differs>;
overrides=<members named by override-of>`.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402
import make_overloads as MO  # noqa: E402

L = S.L
CAT = 'c15-overloads'


def main():
    db = S.load_db(astdb.FUNC_KINDS + astdb.RECORD_KINDS)
    sets, _rows, conflicts, assigned = MO.compute(db)
    if conflicts:
        sys.exit('c15-overloads: make_overloads reports %d conflicts' % len(conflicts))
    rows = []
    for key, nm, ms in sets.sets(21):
        ms = sorted(ms, key=MO.position)
        first = ms[0]
        renamed = sum(1 for c in ms if assigned[c['usr']][0] != nm)
        ov = sum(1 for c in ms if assigned[c['usr']][1].startswith('override-of'))
        pre = ['members=%d' % len(ms), 'renamed=%d' % renamed]
        if ov:
            pre.append('overrides=%d' % ov)
        un = S.unit_of(first['file'])
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        rows.append([first['file'], '%s::%s' % (sets.scope_label[key], nm), 'overload set (more than 20 members)',
                     first['line'], '; '.join(pre)])
    total = sum(int(r[4].split(';')[0].split('=')[1]) for r in rows)
    L.finish(CAT, rows, 'overload sets with more than 20 members (make_overloads.py membership: operators, ctors, '
                        'explicit specializations excluded), %d members in all; names in migration/design/overloads.tsv'
             % total)


if __name__ == '__main__':
    main()
