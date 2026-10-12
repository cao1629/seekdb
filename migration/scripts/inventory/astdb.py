#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""astdb.py -- query helper over the libclang declaration database (stdlib only).

The database is build_release/inventory-ast/decls.jsonl, written by ast_extract.py
(schema: migration/scripts/inventory/README.md, section "ast_extract.py").

Library use (from the repository root):

    import sys
    sys.path.insert(0, 'migration/scripts/inventory')
    import astdb

    db = astdb.AstDB()                                   # whole database (~1 min, several GB)
    db = astdb.AstDB(kinds=astdb.RECORD_KINDS + ('field',))   # only what you need (faster)

    for r in db.by_kind('union'): ...                    # records of some kinds
    rec = db.definition(usr)                             # class/struct/union/enum/var definition
    db.find('oceanbase::common::ObString')               # records with this qualified name
    db.children(usr, kinds=('field',))                   # records whose parent_usr is usr
    db.bases(usr); db.ancestors(usr); db.derived(usr)    # class hierarchy (derived: transitive)
    db.fields(usr, inherited=True)                       # layout-order fields, anonymous members inlined
    db.methods(usr, inherited=False)                     # one record per method USR
    db.overload_sets(min_size=21)                        # {(parent_usr, name): [records]}
    db.enclosing('src/sql/ob_spi.cpp', 1234)             # innermost class/function/enum around a line

Command line (stdlib python, isolated mode):

    python3 -I migration/scripts/inventory/astdb.py find  oceanbase::common::ObString
    python3 -I migration/scripts/inventory/astdb.py fields oceanbase::sql::ObExecContext [--inherited]
    python3 -I migration/scripts/inventory/astdb.py hierarchy oceanbase::sql::ObOpSpec
    python3 -I migration/scripts/inventory/astdb.py overloads --min 21

Notes
  * One record per declaration (usr, kind, file, line): a class has one record per forward
    declaration plus its definition; a method has its in-class declaration and, if defined
    out of line, a second record (out_of_line=1).  The helpers below pick one per USR.
  * USRs are clang's, except anonymous structs/unions whose USR gets '@file:line:col'
    appended (clang gives all anonymous records of one scope the same USR).
  * Implicit template instantiations (ObSEArray<int64_t, 4>) are not records.  A base that
    is one carries `template_usr` (the primary template), which ancestors()/derived() follow.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
DEFAULT_DB = os.path.join(ROOT, 'build_release', 'inventory-ast', 'decls.jsonl')

RECORD_KINDS = ('class', 'struct', 'union', 'class_template', 'partial_specialization')
FUNC_KINDS = ('function', 'method', 'ctor', 'dtor', 'conversion', 'function_template')
METHOD_KINDS = ('method', 'ctor', 'dtor', 'conversion', 'function_template')
ALL_KINDS = RECORD_KINDS + FUNC_KINDS + ('enum', 'enum_constant', 'field', 'typedef', 'alias', 'var', 'friend')


def _pos(r):
    return (r['file'], r['line'], r.get('col', 0))


class AstDB:
    """In-memory view of decls.jsonl with lazily built indexes."""

    def __init__(self, path=DEFAULT_DB, kinds=None, include_generated=True):
        """kinds: iterable of record kinds to load (None = all).  Loading only the kinds a
        script needs is much faster and lighter.  include_generated=False drops records
        from generated files (generated=1)."""
        self.path = path
        self.kinds = tuple(kinds) if kinds else None
        want = set(self.kinds) if self.kinds else None
        needles = ['"kind":"%s"' % k for k in want] if want else None
        self.records = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                if needles is not None and not any(n in line for n in needles):
                    continue
                r = json.loads(line)
                if want is not None and r['kind'] not in want:
                    continue
                if not include_generated and r.get('generated'):
                    continue
                self.records.append(r)
        self._by_usr = None
        self._by_parent = None
        self._by_kind = None
        self._by_qname = None
        self._derived = None
        self._spans = None

    # ---- indexes
    def _index(self, attr, keyfn):
        idx = {}
        for r in self.records:
            k = keyfn(r)
            if k is not None:
                idx.setdefault(k, []).append(r)
        setattr(self, attr, idx)
        return idx

    @property
    def by_usr_index(self):
        return self._by_usr if self._by_usr is not None else self._index('_by_usr', lambda r: r['usr'])

    @property
    def by_parent_index(self):
        return self._by_parent if self._by_parent is not None else self._index(
            '_by_parent', lambda r: r.get('parent_usr'))

    @property
    def by_kind_index(self):
        return self._by_kind if self._by_kind is not None else self._index('_by_kind', lambda r: r['kind'])

    @property
    def by_qname_index(self):
        return self._by_qname if self._by_qname is not None else self._index('_by_qname', lambda r: r['qname'])

    def _need(self, *kinds):
        if self.kinds is not None:
            missing = [k for k in kinds if k not in self.kinds]
            if missing:
                raise ValueError('AstDB was loaded without kinds %s' % ', '.join(missing))

    # ---- basic queries
    def by_kind(self, *kinds):
        """All records of the given kinds, in file/line order."""
        out = []
        for k in kinds:
            out.extend(self.by_kind_index.get(k, []))
        out.sort(key=_pos)
        return out

    def get(self, usr, kinds=None):
        """All records (declarations, definitions) of one USR."""
        rs = self.by_usr_index.get(usr, [])
        return [r for r in rs if kinds is None or r['kind'] in kinds]

    def find(self, qname, kinds=None):
        """Records with this fully qualified name (e.g. 'oceanbase::common::ObString')."""
        rs = self.by_qname_index.get(qname, [])
        return [r for r in rs if kinds is None or r['kind'] in kinds]

    def definition(self, usr, kinds=RECORD_KINDS + ('enum', 'var')):
        """The definition record of a class/struct/union/enum/var (None if never defined).
        Explicit instantiations ('template class X<int>;') are not definitions here."""
        defs = [r for r in self.get(usr, kinds) if r.get('is_definition') and not r.get('explicit_instantiation')]
        if not defs:
            return None
        defs.sort(key=_pos)
        return defs[0]

    def canonical(self, usr, kinds=FUNC_KINDS):
        """One representative record of a function/method USR: the declaration inside its
        class or header (not out_of_line) if any, else the first definition."""
        rs = self.get(usr, kinds)
        if not rs:
            return None
        rs = sorted(rs, key=lambda r: (bool(r.get('out_of_line')), bool(r.get('is_definition')), _pos(r)))
        return rs[0]

    def children(self, parent_usr, kinds=None):
        """Records whose parent_usr is parent_usr (members of a record, constants of an enum,
        declarations of a namespace), in declaration order."""
        rs = [r for r in self.by_parent_index.get(parent_usr, []) if kinds is None or r['kind'] in kinds]
        if rs and all('field_index' in r for r in rs):
            rs.sort(key=lambda r: (r['field_index'], _pos(r)))     # data members: declaration order
        else:
            rs.sort(key=_pos)
        return rs

    # ---- hierarchy
    def bases(self, usr):
        """Base specifiers of the record's definition: [{qname, usr, access, virtual, ...}]."""
        d = self.definition(usr, RECORD_KINDS)
        return list(d.get('bases', [])) if d else []

    def _resolve_base(self, b):
        """USR of the record that a base specifier refers to, following template_usr when the
        base is an implicit instantiation without its own record."""
        u = b.get('usr')
        if u and self.definition(u, RECORD_KINDS) is not None:
            return u
        t = b.get('template_usr')
        if t and self.definition(t, RECORD_KINDS) is not None:
            return t
        return u or t or None

    def ancestors(self, usr):
        """All (transitive) base record USRs, nearest first, each once."""
        self._need(*RECORD_KINDS)
        out, seen, todo = [], {usr}, [usr]
        while todo:
            cur = todo.pop(0)
            for b in self.bases(cur):
                u = self._resolve_base(b)
                if u and u not in seen:
                    seen.add(u)
                    out.append(u)
                    todo.append(u)
        return out

    def derived(self, usr, transitive=True):
        """USRs of records deriving from usr (directly, or transitively when transitive=True).
        A base that is an instantiation of a template also counts as deriving from the template."""
        self._need(*RECORD_KINDS)
        if self._derived is None:
            der = {}
            for r in self.records:
                if r['kind'] in RECORD_KINDS and r.get('is_definition'):
                    for b in r.get('bases', []):
                        for key in (b.get('usr'), b.get('template_usr')):
                            if key:
                                der.setdefault(key, set()).add(r['usr'])
            self._derived = der
        out, todo = set(), [usr]
        while todo:
            cur = todo.pop()
            for d in self._derived.get(cur, ()):
                if d not in out:
                    out.add(d)
                    if transitive:
                        todo.append(d)
        out.discard(usr)
        return out

    # ---- members
    def fields(self, usr, inherited=False, flatten_anonymous=True, _seen=None):
        """Non-static data members of a record in layout order.  inherited=True puts the
        fields of the bases (recursively, in base order) first.  Members of anonymous
        structs/unions are inlined where the anonymous member appears (their records keep
        their own parent_usr).  Static data members are kind 'var' (see vars())."""
        self._need('field', *RECORD_KINDS)
        _seen = _seen if _seen is not None else set()
        if usr in _seen:
            return []
        _seen.add(usr)
        out = []
        if inherited:
            for b in self.bases(usr):
                u = self._resolve_base(b)
                if u:
                    out.extend(self.fields(u, True, flatten_anonymous, _seen))
        d = self.definition(usr, RECORD_KINDS)
        if d is None:
            return out
        mems = []
        # several unrelated definitions can share one USR (same qualified name defined in two
        # .cpp files): then keep only the members written in the chosen definition's file
        same_file = d.get('n_defs', 1) > 1
        for r in self.by_parent_index.get(usr, []):
            if same_file and r['file'] != d['file']:
                continue
            if r['kind'] == 'field':
                mems.append(r)
            elif flatten_anonymous and r['kind'] in ('struct', 'union') and r.get('is_anonymous_member'):
                mems.append(r)
        # field_index numbers fields and anonymous struct/union members together
        mems.sort(key=lambda r: (r.get('field_index', 1 << 30), _pos(r)))
        for m in mems:
            if m['kind'] == 'field':
                out.append(m)
            else:
                out.extend(self.fields(m['usr'], False, True, _seen))
        return out

    def vars(self, usr):
        """Static data members of a record (one record per USR, the in-class declaration first)."""
        seen, out = set(), []
        for r in sorted(self.by_parent_index.get(usr, []), key=lambda r: (bool(r.get('is_definition')), _pos(r))):
            if r['kind'] == 'var' and r['usr'] not in seen:
                seen.add(r['usr'])
                out.append(r)
        return sorted(out, key=_pos)

    def methods(self, usr, inherited=False, kinds=METHOD_KINDS):
        """Member functions of a record, one record per USR (see canonical()).  With
        inherited=True the methods of all ancestors follow (constructors and destructors
        excluded), minus the ones overridden here."""
        self._need(*kinds)
        out, seen = [], set()
        for r in self.by_parent_index.get(usr, []):
            if r['kind'] in kinds and r['usr'] not in seen:
                seen.add(r['usr'])
                out.append(self.canonical(r['usr'], kinds) or r)
        out.sort(key=_pos)
        if inherited:
            overridden = set()
            for m in out:
                overridden.update(m.get('overrides', []))
            for a in self.ancestors(usr):
                for m in self.methods(a, False, kinds):
                    if m['kind'] in ('ctor', 'dtor') or m.get('template_kind') in ('ctor', 'dtor'):
                        continue                  # constructors and destructors are not inherited
                    if m['usr'] not in seen and m['usr'] not in overridden:
                        seen.add(m['usr'])
                        overridden.update(m.get('overrides', []))
                        out.append(m)
        return out

    def enclosing(self, file, line, kinds=RECORD_KINDS + FUNC_KINDS + ('enum',)):
        """Innermost record/function/enum definition whose lines [line, end_line] contain
        file:line (e.g. the class a regex hit sits in), or None.  Only definitions carry
        end_line; macro-generated declarations span their macro invocation."""
        if self._spans is None:
            spans = {}
            for r in self.records:
                if 'end_line' in r:
                    spans.setdefault(r['file'], []).append(r)
            for v in spans.values():
                v.sort(key=lambda r: (r['line'], -r['end_line']))
            self._spans = spans
        best = None
        for r in self._spans.get(file, ()):
            if r['line'] > line:
                break
            if r['end_line'] >= line and r['kind'] in kinds:
                if best is None or (r['line'], -r['end_line']) >= (best['line'], -best['end_line']):
                    best = r
        return best

    def overload_sets(self, min_size=2, kinds=FUNC_KINDS):
        """{(parent_usr, name): [one record per USR]} for every name declared at least
        min_size times with distinct USRs in the same scope (class or namespace)."""
        self._need(*kinds)
        sets = {}
        for r in self.records:
            if r['kind'] in kinds:
                sets.setdefault((r.get('parent_usr', ''), r['name']), {}).setdefault(r['usr'], []).append(r)
        out = {}
        for key, by_usr in sets.items():
            if len(by_usr) >= min_size:
                reps = [self.canonical(u, kinds) for u in by_usr]
                out[key] = sorted(reps, key=_pos)
        return out


# --------------------------------------------------------------------------- CLI

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='query decls.jsonl')
    ap.add_argument('--db', default=DEFAULT_DB)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('find')
    p.add_argument('qname')
    p = sub.add_parser('fields')
    p.add_argument('qname')
    p.add_argument('--inherited', action='store_true')
    p = sub.add_parser('hierarchy')
    p.add_argument('qname')
    p = sub.add_parser('overloads')
    p.add_argument('--min', type=int, default=21)
    o = ap.parse_args(argv)
    if o.cmd == 'find':
        db = AstDB(o.db)
        for r in sorted(db.find(o.qname), key=_pos):
            print(json.dumps(r, sort_keys=True, ensure_ascii=False))
    elif o.cmd == 'fields':
        db = AstDB(o.db, kinds=RECORD_KINDS + ('field',))
        for d in db.find(o.qname, RECORD_KINDS):
            if not d.get('is_definition'):
                continue
            print('# %s  %s:%d  size=%s' % (d['qname'], d['file'], d['line'], d.get('size')))
            for f in db.fields(d['usr'], inherited=o.inherited):
                print('%-40s %-60s %s:%d' % (f['parent_qname'].split('::')[-1] + '::' + f['name'],
                                             f.get('type', ''), f['file'], f['line']))
    elif o.cmd == 'hierarchy':
        db = AstDB(o.db, kinds=RECORD_KINDS)
        for d in db.find(o.qname, RECORD_KINDS):
            if not d.get('is_definition'):
                continue
            print('# %s  %s:%d' % (d['qname'], d['file'], d['line']))
            for u in db.ancestors(d['usr']):
                dd = db.definition(u, RECORD_KINDS)
                print('  base    ', dd['qname'] if dd else u)
            for u in sorted(db.derived(d['usr'])):
                dd = db.definition(u, RECORD_KINDS)
                print('  derived ', dd['qname'] if dd else u)
    elif o.cmd == 'overloads':
        db = AstDB(o.db, kinds=FUNC_KINDS)
        sets = db.overload_sets(min_size=o.min)
        for (pusr, name), rs in sorted(sets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print('%d\t%s\t%s' % (len(rs), rs[0].get('parent_qname', ''), name))


if __name__ == '__main__':
    _main(sys.argv[1:])
