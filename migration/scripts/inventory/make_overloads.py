#!/usr/bin/env python3
"""make_overloads.py: migration/design/overloads.tsv, the Rust name of every member of every C++ overload
set (RULEBOOK §4 "重载", §2.13 receiver row, §5.1).

Members: astdb canonical records (one per USR) of kind function, method and function_template, minus
generated files, constructors/destructors/conversions, operator functions (§2.13 maps them to traits,
`call`, `at`, `assign`), explicit specializations (`sig` spelled `name<>(…)`: they implement the template,
they are not overloads) and deleted functions.  A set = same parent scope (class or namespace) + same name;
free functions with internal linkage are grouped per file (static functions of different .cpp files are
not overloads of each other; their glob re-export clashes are in symbols.tsv).

Rules (column `rule`; `+` joins the steps that fired):
  first-declared        the member declared first keeps the name.  "First": the units.tsv order of the
                        member's unit (dependency order), then header before implementation file, then line
  distinguishing-types  name + `_` + the snake case of the parameter types (as written) that differ, by
                        position, from the first-declared member's (canonical comparison); type spelling:
                        const/volatile/&/*/namespace qualifiers dropped, `Ob` prefix dropped from every word,
                        template arguments kept as words (`const ObIArray<ObRawExpr *> &` -> `i_array_raw_expr`)
  arity                 no distinguishing type (the parameters are a prefix of the first member's):
                        name + `_` + parameter count
  arity-suffix          the name is taken (by another member of the scope): append `_` + parameter count
  full-signature        still taken: name + `_` + every parameter type
  ordinal               still taken: name + `_v<k>` (k = declaration order in the set)
  const-pair            two members with the same parameters differing only in const: the const one keeps the
                        name chosen for the pair, the non-const one gets `_mut` (RULEBOOK §2.13), whatever the order
  override-of:<Base::f> a method overriding a virtual method (astdb `overrides`, plus exact-signature matches in
                        derived records for class templates whose overrides libclang does not link) takes the
                        overridden method's Rust name: a trait method has one name.  Such rows are emitted even when
                        the overrider is alone in its own scope but the inherited name differs from its C++ name.
Scopes are processed base classes first; in a scope, single (non-overloaded) names are reserved before sets
are named, and sets are named in declaration order.  The script exits non-zero if two members of one scope
get the same Rust name (overriders taking the same inherited name excepted: they implement trait methods).
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
OUT = os.path.join(S.DESIGN, 'overloads.tsv')
HEADER = ['scope', 'name', 'signature', 'file', 'line', 'is_const', 'rust_name', 'rule']
RX_OPERATOR = re.compile(r'^operator(?![A-Za-z0-9_])')


def eligible(c):
    if c is None or c.get('generated'):
        return False
    if c['kind'] not in ('function', 'method', 'function_template'):
        return False
    if c['kind'] == 'function_template' and c.get('template_kind') in ('ctor', 'dtor', 'conversion'):
        return False
    if RX_OPERATOR.match(c['name']) or c['name'].startswith('~'):
        return False
    if '<>(' in (c.get('sig') or ''):
        return False
    if c.get('is_deleted'):
        return False
    return True


def sig_key(c):
    return (tuple(p.get('canonical_type') or p.get('type') or '' for p in c.get('params', []) or []),
            c.get('ref_qual') or '', bool(c.get('is_variadic')))


def position(c):
    un = S.unit_of(c['file'])
    order = un.order if un is not None else 1 << 30
    hdr = 0 if c['file'].endswith(('.h', '.hpp', '.ipp', '.inc')) else 1
    return (order, hdr, c['file'], c['line'], c.get('col', 0), c['usr'])


def type_token(t):
    t = re.sub(r'\b(?:const|volatile|struct|class|enum|typename|union|restrict|__restrict)\b', ' ', t or '')
    t = t.replace('...', ' ')
    t = re.sub(r'\[[^\]]*\]', ' arr ', t)
    t = re.sub(r'\b(?:[A-Za-z_]\w*\s*::\s*)+', '', t)
    words = re.findall(r'[A-Za-z_]\w*|\d+', t)
    toks = [S.snake(S.strip_ob(w)) for w in words]
    return '_'.join(x for x in toks if x)


class Sets(object):
    """Overload membership shared by make_overloads.py and sweep_c15_overloads.py."""

    def __init__(self, db):
        self.db = db
        self.by_scope = collections.OrderedDict()     # scope key -> {name: [canonical records]}
        self.scope_label = {}
        self.scope_record = {}
        seen = set()
        recs = []
        for r in db.records:
            if r['kind'] not in ('function', 'method', 'function_template') or r['usr'] in seen:
                continue
            seen.add(r['usr'])
            c = db.canonical(r['usr'])
            if eligible(c):
                recs.append(c)
        recs.sort(key=position)
        for c in recs:
            pu = c.get('parent_usr') or ''
            prec = db.definition(pu, astdb.RECORD_KINDS) if pu else None
            key = pu
            label = S.short_qname(c.get('parent_qname') or '') or '(global)'
            if prec is None and c.get('linkage') == 'internal' and c['kind'] in ('function', 'function_template'):
                key = '%s@%s' % (pu, c['file'])
                label = '%s [internal: %s]' % (label, c['file'])
            self.by_scope.setdefault(key, collections.OrderedDict()).setdefault(c['name'], []).append(c)
            self.scope_label[key] = label
            self.scope_record[key] = prec['usr'] if prec is not None else None

    def sets(self, min_size=2):
        for key, names in self.by_scope.items():
            for nm, ms in names.items():
                if len(ms) >= min_size:
                    yield key, nm, ms


def override_links(db, sets):
    """usr -> [overridden usrs] (direct astdb links + exact-signature links in derived records)."""
    links = collections.defaultdict(list)
    by_parent_name = collections.defaultdict(list)
    allm = []
    for key, names in sets.by_scope.items():
        for nm, ms in names.items():
            for c in ms:
                allm.append(c)
                if c.get('parent_usr'):
                    by_parent_name[(c['parent_usr'], nm)].append(c)
    for c in allm:
        for o in c.get('overrides', []) or []:
            if o not in links[c['usr']]:
                links[c['usr']].append(o)
    derived_cache = {}
    for v in allm:
        if not v.get('is_virtual') or not v.get('parent_usr'):
            continue
        pu = v['parent_usr']
        if pu not in derived_cache:
            try:
                derived_cache[pu] = sorted(db.derived(pu))
            except ValueError:
                derived_cache[pu] = []
        k = sig_key(v) + (bool(v.get('is_const')),)
        for du in derived_cache[pu]:
            for m in by_parent_name.get((du, v['name']), ()):
                if m['usr'] != v['usr'] and sig_key(m) + (bool(m.get('is_const')),) == k and v['usr'] not in links[m['usr']]:
                    if not links[m['usr']]:
                        links[m['usr']].append(v['usr'])
    return links


def compute(db):
    sets = Sets(db)
    links = override_links(db, sets)
    depth = {}

    def scope_depth(key):
        if key not in depth:
            ru = sets.scope_record.get(key)
            depth[key] = len(db.ancestors(ru)) if ru else -1
        return depth[key]

    order = sorted(sets.by_scope, key=lambda k: (scope_depth(k), sets.scope_label[k], k))
    assigned = {}            # usr -> (rust_name, rule)
    rows = []
    conflicts = []
    for key in order:
        names = sets.by_scope[key]
        used = {}            # rust name -> usr (inherent members of this scope)
        fixed = {}
        for nm, ms in names.items():
            for c in ms:
                for o in links.get(c['usr'], ()):
                    if o in assigned:
                        oc = db.canonical(o)
                        fixed[c['usr']] = (assigned[o][0], 'override-of:%s::%s' % (
                            S.short_qname(oc.get('parent_qname') or '?'), oc['name']))
                        break
        # singletons first (reserve), then sets in declaration order
        for nm, ms in names.items():
            if len(ms) == 1:
                c = ms[0]
                if c['usr'] in fixed:
                    rn, rule = fixed[c['usr']]
                    assigned[c['usr']] = (rn, rule)
                    if rn != nm:
                        rows.append(row(sets, key, c, rn, rule))
                else:
                    assigned[c['usr']] = (nm, 'single')
                    used.setdefault(nm, c['usr'])
        for c_usr in sorted(fixed):
            used.setdefault(fixed[c_usr][0], c_usr)      # inherent members avoid the trait method names too
        pending = [(position(ms[0]), nm, ms) for nm, ms in names.items() if len(ms) >= 2]
        for _pos, nm, ms in sorted(pending):
            name_set(sets, key, nm, ms, fixed, used, assigned, rows)
        # uniqueness: no two members of the scope with one Rust name, unless both are overriders
        seen = {}
        for nm, ms in names.items():
            for c in ms:
                rn, rule = assigned[c['usr']]
                ov = rule.startswith('override-of')
                if rn in seen and seen[rn][0] != c['usr'] and not (ov and seen[rn][1]):
                    conflicts.append((sets.scope_label[key], rn, c['usr'], seen[rn][0]))
                if rn not in seen or not ov:
                    seen[rn] = (c['usr'], ov)
    rows.sort(key=lambda r: (r[0], r[1], r[3], int(r[4]), r[2]))
    return sets, rows, conflicts, assigned


def row(sets, key, c, rn, rule):
    sig = c.get('sig') or c['name']
    if c.get('is_const'):
        sig += ' const'
    if c.get('ref_qual'):
        sig += ' ' + str(c['ref_qual'])
    return [sets.scope_label[key], c['name'], sig, c['file'], str(c['line']), '1' if c.get('is_const') else '0', rn, rule]


def name_set(sets, key, nm, ms, fixed, used, assigned, rows):
    ms = sorted(ms, key=position)
    first = ms[0]
    first_ps = first.get('params', []) or []
    classes = collections.OrderedDict()
    for c in ms:
        classes.setdefault(sig_key(c), []).append(c)
    # overriders keep the inherited names; they are reserved before the others are named
    for c in ms:
        if c['usr'] in fixed:
            assigned[c['usr']] = fixed[c['usr']]
    for k, (sk, cs) in enumerate(classes.items()):
        free = [c for c in cs if c['usr'] not in fixed]
        if not free:
            continue
        ps = free[0].get('params', []) or []
        cands = []
        if first in cs:
            cands.append((nm, 'first-declared'))
            others = [c for c in ms if c not in cs]
            ref_ps = (others[0].get('params', []) or []) if others else []
        else:
            ref_ps = first_ps
        toks = []
        for i, p in enumerate(ps):
            if i < len(ref_ps) and (ref_ps[i].get('canonical_type') or '') == (p.get('canonical_type') or ''):
                continue
            t = type_token(p.get('type') or p.get('canonical_type'))
            if t:
                toks.append(t)
        if toks:
            base, rule = '%s_%s' % (nm, '_'.join(toks)), 'distinguishing-types'
        else:
            base, rule = '%s_%d' % (nm, len(ps)), 'arity'
        cands.append((base, rule))
        full = '%s_%s' % (nm, '_'.join(type_token(p.get('type') or p.get('canonical_type')) for p in ps) or 'void')
        cands += [('%s_%d' % (base, len(ps)), rule + '+arity-suffix'), (full, 'full-signature')]
        cands += [('%s_v%d' % (nm, ms.index(free[0]) + 1 + j), 'ordinal') for j in range(0, 60)]
        pair = len(free) == 2 and sum(1 for c in free if c.get('is_const')) == 1 and len(cs) == 2
        others = [c for c in free]
        if pair:
            cc = [c for c in free if c.get('is_const')][0]
            nc = [c for c in free if not c.get('is_const')][0]
            for cand, r in cands:
                if cand not in used and cand + '_mut' not in used:
                    assigned[cc['usr']] = (cand, r + '+const-pair')
                    assigned[nc['usr']] = (cand + '_mut', r + '+const-pair')
                    used[cand] = cc['usr']
                    used[cand + '_mut'] = nc['usr']
                    break
            continue
        # one or more members (identical parameters, not a const pair): each takes the next free candidate
        for c in others:
            for cand, r in cands:
                if cand not in used:
                    assigned[c['usr']] = (cand, r)
                    used[cand] = c['usr']
                    break
    for c in ms:
        rn, r = assigned[c['usr']]
        rows.append(row(sets, key, c, rn, r))


def main():
    db = S.load_db(astdb.FUNC_KINDS + astdb.RECORD_KINDS)
    sets, rows, conflicts, _assigned = compute(db)
    if conflicts:
        for scope, rn, a, b in conflicts[:20]:
            print('CONFLICT in %s: %s <- %s / %s' % (scope, rn, a, b))
        sys.exit('make_overloads: %d rust_name conflicts' % len(conflicts))
    L.write_tsv(OUT, HEADER, rows)
    nsets = sum(1 for _ in sets.sets(2))
    rules = collections.Counter(r[7].split(':')[0] for r in rows)
    print('overloads.tsv: %d rows, %d sets (>=2 members); rules %s' % (len(rows), nsets, dict(sorted(rules.items()))))


if __name__ == '__main__':
    main()
