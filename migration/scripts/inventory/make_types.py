#!/usr/bin/env python3
"""make_types.py: migration/design/types.tsv (RULEBOOK §5.1, §2.13, 0.5-0.7), one row per first-party record
definition (class/struct/union, class templates, partial and explicit specializations; forward declarations
and explicit instantiations skipped; generated files skipped).  Records of files no TU parses come from the
regex scope parser (kind suffixed '-regex').

Columns (rules in README.md, section "types.tsv"):
  class                fully qualified name (template arguments kept for specializations)
  file, line           definition
  kind                 class | struct | union | class_template | partial_specialization | explicit_specialization,
                       ':anonymous-member' / ':anonymous' for unnamed records, '-regex' for regex rows
  rust_struct          data struct name: the C++ name (innermost name, specialization arguments kept);
                       empty for pure interfaces and anonymous records
  rust_trait           '<name>Virt' for classes with a vtable and data (own or inherited); '<name>' for pure
                       interfaces (vtable, no data in the record or its bases); empty without a vtable.
                       ObExprOperator hierarchy: both columns say 'ExprOp/OpState (0.6)'
  bases                ';'-separated base specifiers ('virtual ' prefix for virtual bases), namespaces dropped
  base_fields          'base=<B>' for the first base with data, 'base_<snake>=<B>' for the other bases with data
  isa_set              for records in a downcast hierarchy: 'as_<variant>=<class>;<derived>;...' (the record and
                       all its transitive derived records); hierarchies: RawExpr, Stmt, LogOp, Path, OpSpec,
                       ExtraInfo, ExprOpCtx and every other first-party hierarchy that the code downcasts into
                       (static_cast/dynamic_cast to a proper descendant at >= 10 sites) and whose root has an
                       enum member (a type tag)
  lifetime_candidates  members that may need 'q (closure): '<member>(str|ref|ptr|alloc|via:<Record>)', bases as
                       'base(via:<B>)' / 'base_<snake>(via:<B>)'
  lifetime_class       only where a rule decides it: long-lived (module; session), plan (ObPhysicalPlan,
                       ObOpSpec family, ObExpr), query (compile-time IR; ObExprOperator hierarchy -> OpState)
  shared               yes: module (GCTX/server_service/get_instance), session (0.5), RULEBOOK 0.9 Arc types,
                       lock members, atomic members (std::atomic/volatile, or ATOMIC_* on a member name in the
                       unit's files), singleton static (a non-const static of its own type inside the class or
                       inside one of its methods); candidate: other non-const statics, refcounted, timer task,
                       ObLinkHashMap value, thread/pool, ObPhysicalPlan, direct by-value member of or pointed to
                       by a module; statics named ALL_CAPS (constants by convention) give no signal; reason in a
                       trailing '(...)'
  disposition          units.tsv disposition of the definition file
  guard_methods        empty (c07)
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

OUT = os.path.join(S.REPO, 'migration', 'design', 'types.tsv')
HEADER = ['class', 'file', 'line', 'kind', 'rust_struct', 'rust_trait', 'bases', 'base_fields', 'isa_set',
          'lifetime_candidates', 'lifetime_class', 'shared', 'disposition', 'guard_methods']
EXPROP_NOTE = 'ExprOp/OpState (0.6)'
# RULEBOOK 0.9 Arc table (tx ctx, tx desc, table lock objects, tx data, tablet/table/sstable handles, t3m)
ARC_09 = ('oceanbase::transaction::ObTransCtx', 'oceanbase::transaction::ObPartTransCtx',
          'oceanbase::transaction::ObTxDesc', 'oceanbase::transaction::tablelock::ObOBJLock',
          'oceanbase::storage::ObTxData', 'oceanbase::storage::ObTablet', 'oceanbase::storage::ObITable',
          'oceanbase::blocksstable::ObSSTable', 'oceanbase::memtable::ObMemtable',
          'oceanbase::storage::ObStorageMetaMemMgr')
VARIANT_AFFIX = {'RawExpr': ('', 'RawExpr'), 'Stmt': ('', 'Stmt'), 'LogOp': ('Log', ''), 'Path': ('', 'Path'),
                 'OpSpec': ('', 'Spec'), 'ExtraInfo': ('', 'ExtraInfo'), 'ExprOpCtx': ('', 'Ctx')}
RX_DOWNCAST = re.compile(r'\b(?:static_cast|dynamic_cast)\s*<\s*(?:const\s+)?([A-Za-z_][\w:]*)\s*[*&]\s*>')
RX_ATOMIC = re.compile(r'\bATOMIC_\w+\s*\(\s*&?\s*\(?\s*(?:this\s*->\s*)?(?:[A-Za-z_]\w*\s*(?:\.|->)\s*)*'
                       r'([A-Za-z_]\w*)')
RX_LOCAL_STATIC = re.compile(r'(?m)^[ \t]*static\s+(?!const\b|constexpr\b|inline\b|void\b|int\b|bool\b|char\b|'
                             r'thread_local\b|__thread\b)((?:[A-Za-z_][\w]*::)*[A-Za-z_]\w*(?:\s*<[^;(){}]*>)?)\s*'
                             r'(\*?)\s*([A-Za-z_]\w*)\s*[;({=\[]')
RX_REFCNT_METHOD = re.compile(r'^(?:inc_ref\w*|dec_ref\w*|get_ref\w*|inc_ref_cnt|dec_ref_cnt|retain|add_ref|'
                              r'release_ref)$')
RX_CONST_NAME = re.compile(r'^[A-Z][A-Z0-9_]*$')
RX_REFCNT_FIELD = re.compile(r'^(?:ref_cnt|ref_count|ref_cnt_|ref_count_|ref_|refcnt_|ref_cnt__)$')


def kind_of(r):
    k = r['kind']
    if k in ('class', 'struct', 'union') and r.get('specialization_of'):
        k = 'explicit_specialization'
    if r.get('is_anonymous') or not r.get('name'):
        k += ':anonymous-member' if r.get('is_anonymous_member') else ':anonymous'
    return k


def variant(label, name):
    pre, suf = VARIANT_AFFIX.get(label, ('', ''))
    n = re.sub(r'<.*', '', name)
    if n.startswith('Ob'):
        n = n[2:]
    if pre and n.startswith(pre) and len(n) > len(pre):
        n = n[len(pre):]
    if suf and n.endswith(suf) and len(n) > len(suf):
        n = n[:-len(suf)]
    return S.snake(n)


def find_hierarchies(m):
    """[(root usr, label)]: the fixed RULEBOOK hierarchies plus tagged hierarchies with >= 10 downcasts."""
    fixed = []
    for q, lab in S.ISA_ROOTS:
        u = m.resolve(q)
        if u:
            fixed.append((u, lab))
    fixed_roots = set(u for u, _ in fixed)
    counts = collections.Counter()
    for p in L.first_party():
        raw = L.raw(p)
        if '_cast' not in raw:
            continue
        for mm in RX_DOWNCAST.finditer(L.stripped(p)):
            for u in m.resolve_written(mm.group(1)):
                counts[u] += 1
    root_counts = collections.Counter()
    for u, n in counts.items():
        anc = [a for a in m.ancestors(u) if a in m.defs and not m.is_transparent(a, m.defs[a]['qname'])]
        if not anc:
            continue
        root_counts[anc[-1]] += n
    extra = []
    for root, n in sorted(root_counts.items()):
        if n < 10 or root in fixed_roots or root in m.exprop:
            continue
        if any(root in m.ancestors(fr) or fr in m.ancestors(root) for fr in fixed_roots):
            continue
        r = m.defs[root]
        if len(m.derived(root)) < 2:
            continue
        has_tag = any(f.get('canonical_kind') == 'enum' or f.get('type_kind') == 'enum' for f in m.fields(root, True))
        if not has_tag:
            continue
        extra.append((root, r['name']))
    return fixed + sorted(extra, key=lambda x: m.defs[x[0]]['qname'])


def lifetime_closure(m):
    """{usr: [(member, label)]} for every record whose members (transitively) may need 'q."""
    skip_targets = m.modules | m.ir | m.plan_nodes
    direct, valtgt = {}, {}
    for u in m.defs:
        trig, vt = [], []
        for f in m.fields(u):
            done = False
            for k, t, h in m.field_edges(f):
                if done:
                    break
                if k == 'str':
                    trig.append((f['name'], 'str'))
                    done = True
                elif k in ('ref', 'ptr'):
                    if t == 'fn':
                        continue
                    if t in m.allocators:
                        trig.append((f['name'], 'alloc'))
                        done = True
                    elif t in skip_targets:
                        continue
                    else:
                        trig.append((f['name'], k))
                        done = True
                elif k == 'value' and t in m.defs:
                    vt.append((f['name'], t))
        direct[u] = trig
        valtgt[u] = vt
    bases = {}
    for u, r in m.defs.items():
        bl = []
        names = []
        for b in r.get('bases', []):
            v = m.resolve_base(b)
            if v in m.defs:
                bl.append(v)
        bases[u] = bl
    needs = dict((u, bool(direct[u])) for u in m.defs)
    order = sorted(m.defs)
    changed = True
    while changed:
        changed = False
        for u in order:
            if needs[u]:
                continue
            if any(needs.get(t) for _, t in valtgt[u]) or any(needs.get(b) for b in bases[u]):
                needs[u] = True
                changed = True
    out = {}
    for u in order:
        if not needs[u]:
            continue
        lst = list(direct[u])
        seen = set(n for n, _ in lst)
        for n, t in valtgt[u]:
            if needs.get(t) and n not in seen:
                lst.append((n, 'via:%s' % m.chain(t)))
                seen.add(n)
        out[u] = lst
    return out, bases, needs


def base_field_names(m, r):
    """[(field name, base label)] for bases with data, and the bases column."""
    cols, fields = [], []
    first = True
    used = set()
    for b in r.get('bases', []):
        v = m.resolve_base(b)
        label = m.strip_ns(b.get('qname', ''))
        cols.append(('virtual ' if b.get('virtual') else '') + label)
        data = m.has_data(v) if v in m.defs else bool(b.get('dependent'))
        if not data:
            continue
        if first:
            fname = 'base'
            first = False
        else:
            fname = 'base_%s' % S.snake(re.sub(r'<.*', '', label).split('::')[-1])
            k = 2
            while fname in used:
                fname = '%s_%d' % (fname.rstrip('_0123456789') if k > 2 else fname, k)
                k += 1
        used.add(fname)
        fields.append((fname, label + ('(dependent)' if v not in m.defs else ''), v))
    return cols, fields


def main():
    m = S.Model(funcs=True, vars_=True)
    m.add_service_modules()
    hier = find_hierarchies(m)
    isa = {}
    for root, lab in hier:
        for u in [root] + m.derived(root):
            if u in isa:
                continue
            members = [u] + m.derived(u)
            names = sorted(set(m.chain(x) for x in members if x in m.defs))
            first = m.chain(u)
            names = [first] + [n for n in names if n != first]
            isa[u] = 'as_%s=%s' % (variant(lab, m.defs[u]['name'] if u in m.defs else first), ';'.join(names))
    lt, bases_map, needs = lifetime_closure(m)
    # ------------------------------------------------------------ shared signals
    why = collections.defaultdict(list)
    weak = collections.defaultdict(list)
    for u in m.modules:
        why[u].append('module:%s' % '+'.join(sorted(m.module_why[u])))
    for u in m.session:
        why[u].append('session Arc (0.5)')
    for q in ARC_09:
        for u in m.family(q):
            if u in m.defs:
                why[u].append('Arc (0.9: %s)' % q.split('::')[-1])
    # statics: namespace-scope and static data members (AST), function-local statics (regex)
    for r in m.db.records:
        if r['kind'] != 'var' or r.get('generated') or r.get('is_thread_local'):
            continue
        if not (r.get('is_static') or r.get('storage') in ('static', 'extern') or
                r.get('parent_usr') not in m.rec_any):
            continue
        ct = r.get('canonical_type', '') or ''
        if r.get('is_const') or ct.startswith('const ') or re.search(r'\bconst\s*\*?$', ct):
            continue
        ck = r.get('canonical_kind')
        tgt = None
        if ck == 'record':
            tgt = m.resolve(ct)
        elif ck == 'pointer' and r.get('pointee_kind') == 'record' and not r.get('pointee_const'):
            tgt = m.resolve(r.get('pointee_qname'))
        if tgt in m.defs and not RX_CONST_NAME.match(r['name']):
            singleton = r.get('is_member') and r.get('parent_usr') == tgt
            (why if singleton else weak)[tgt].append('%s %s' % ('singleton static' if singleton else 'static',
                                                              r['name']))
    for p in L.first_party():
        raw = L.raw(p)
        if 'static' not in raw:
            continue
        text = L.stripped(p)
        for mm in RX_LOCAL_STATIC.finditer(text):
            line = L.line_of(p, mm.start(1))
            enc = m.db.enclosing(p, line, kinds=S.FK)
            if enc is None or enc.get('line', line) == line:
                continue
            if RX_CONST_NAME.match(mm.group(3)):
                continue
            for u in m.resolve_written(mm.group(1)):
                singleton = enc.get('parent_usr') == u
                (why if singleton else weak)[u].append('%s %s' % ('singleton local static' if singleton
                                                                  else 'local static', mm.group(3)))
    # locks and atomics
    atomic_names = collections.defaultdict(set)
    for p in L.first_party():
        if 'ATOMIC_' not in L.raw(p):
            continue
        for mm in RX_ATOMIC.finditer(L.stripped(p)):
            atomic_names[p].add(mm.group(1))
    for u, r in m.defs.items():
        lock_f, atom_f = [], []
        unit = m.unit(r['file'])
        files = unit['members'].split(',') if unit else [r['file']]
        names = set()
        for fp in files:
            names |= atomic_names.get(fp, set())
        for f in m.fields(u):
            es = m.field_edges(f)
            ct = f.get('canonical_type', '') or ''
            if any(k in ('value', 'infra') and t in m.locks and h in ('direct', 'array') for k, t, h in es) or \
                    S.LOCK_CANON_RX.search(ct):
                lock_f.append(f['name'])
            if S.ATOMIC_CANON_RX.search(ct) or f['name'] in names:
                atom_f.append(f['name'])
        if lock_f:
            why[u].append('locks:%s' % '+'.join(lock_f[:4]))
        if atom_f:
            why[u].append('atomics:%s' % '+'.join(atom_f[:4]))
        # weak signals
        meths = [x['name'] for x in m.db.by_parent_index.get(u, []) if x['kind'] in S.FK]
        if any(RX_REFCNT_METHOD.match(n) for n in meths) or any(RX_REFCNT_FIELD.match(f['name']) for f in m.fields(u)):
            weak[u].append('refcounted')
    timer_bases = set(r['parent_usr'] for r in m.db.records if r['kind'] == 'method' and
                      r['name'] == 'runTimerTask' and r.get('is_pure') and r.get('parent_usr') in m.defs)
    lhv = m.resolve('oceanbase::common::LinkHashValue')
    runnable = set()
    for q in ('oceanbase::lib::Threads', 'oceanbase::common::ObSimpleThreadPoolBase', 'oceanbase::lib::TGRunnable',
              'oceanbase::share::ObReentrantThread', 'oceanbase::lib::ThreadPool'):
        runnable.update(m.family(q))
    plan = set(m.family('oceanbase::sql::ObPhysicalPlan'))
    mod_value, module_ptr_held = set(), set()
    for u in m.modules:
        for f, es in m.member_edges(u):
            for k, t, h in es:
                if k in ('ptr', 'ref') and t in m.defs and t not in m.modules:
                    module_ptr_held.add(t)
                elif k == 'value' and h == 'direct' and t in m.defs and t not in m.modules:
                    mod_value.add(t)
    for u in m.defs:
        anc = m.ancestors(u)
        if any(a in timer_bases for a in anc):
            weak[u].append('timer task (2.7 Arc<dyn TimerTask>)')
        if lhv and lhv in anc:
            weak[u].append('ObLinkHashMap value (2.7 Arc<V>)')
        if u in runnable:
            weak[u].append('thread/pool')
        if u in plan:
            weak[u].append('Arc<PhysicalPlan> after build (0.6)')
        if u in mod_value:
            weak[u].append('held by value in a module')
        if u in module_ptr_held:
            weak[u].append('pointed to by a module')
    # ------------------------------------------------------------ rows
    rows = []
    for r in m.all_defs:
        if r.get('generated'):
            continue
        u = r['usr']
        kind = kind_of(r)
        anon = ':anonymous' in kind
        label = '' if anon else m.rec_label(r)
        if u in m.exprop:
            rs, rt = EXPROP_NOTE, EXPROP_NOTE
        elif anon:
            rs, rt = '', ''
        elif r.get('has_vtable'):
            if m.has_data(u) or (r.get('has_dependent_base') and r.get('n_fields')):
                rs, rt = label, label + 'Virt'
            else:
                rs, rt = '', label
        else:
            rs, rt = label, ''
        bcols, bfields = base_field_names(m, r)
        lc = []
        if u in lt:
            for n, lab in lt[u]:
                lc.append('%s(%s)' % (n, lab))
            for fname, blab, v in bfields:
                if v in m.defs and needs.get(v):
                    lc.append('%s(via:%s)' % (fname, m.chain(v)))
        ltc = ''
        if u in m.modules:
            ltc = 'long-lived (module)'
        elif u in m.session:
            ltc = 'long-lived (session, 0.5/0.7)'
        elif u in m.plan_nodes:
            ltc = 'plan (0.6 runtime plan)'
        elif u in m.ir:
            ltc = 'query (0.6 QueryArena IR node)'
        elif u in m.exprop:
            ltc = 'query (0.6 OpState on the RawExpr node)'
        sh = ''
        if why.get(u):
            sh = 'yes (%s)' % '; '.join(sorted(set(why[u]))[:6])
        elif weak.get(u):
            sh = 'candidate (%s)' % '; '.join(sorted(set(weak[u]))[:6])
        rows.append([r['qname'], r['file'], r['line'], kind, rs, rt, ';'.join(bcols),
                     ';'.join('%s=%s' % (fn, bl) for fn, bl, v in bfields), isa.get(u, ''), ','.join(lc), ltc, sh,
                     m.disposition(r['file']), ''])
    # regex rows for files no TU parses
    nre = 0
    for p in S.unparsed_files():
        try:
            scs = L.scopes(p)
        except Exception:  # noqa: BLE001
            continue
        for s in scs:
            if s.kind != 'class' or not s.name:
                continue
            chain = [c.name for c in L.scope_chain(p, s.open + 1) if c.kind in ('namespace', 'class') and c.name]
            head = ' '.join(s.header.split())
            mk = re.search(r'\b(class|struct|union)\b', head)
            kind = (mk.group(1) if mk else 'class') + '-regex'
            if re.search(r'\btemplate\s*<', head):
                kind = 'class_template-regex'
            body = L.structural_text(p)[s.open:s.close or s.open]
            has_virtual = bool(re.search(r'\bvirtual\b', body))
            bases = ''
            mb = re.search(r'(?<!:):(?!:)(.*)$', re.sub(r'\btemplate\s*<[^{}]*?>', '', head))
            if mb:
                bases = ';'.join(' '.join(x.replace('public', '').replace('protected', '').replace('private', '')
                                          .split()) for x in S.split_top(mb.group(1)) if x.strip())
            rows.append(['::'.join(chain), p, L.line_of(p, s.open), kind, s.name,
                         (s.name + 'Virt') if has_virtual else '', bases, '', '', '', '', '', m.disposition(p), ''])
            nre += 1
    rows.sort(key=lambda x: (x[1], int(x[2]), x[0]))
    seen = set()
    for x in rows:
        k = (x[0], x[1], x[2])
        if k in seen:
            sys.exit('make_types: duplicate key %r' % (k,))
        seen.add(k)
    S.L.write_tsv(OUT, HEADER, rows)
    st = collections.Counter(x[11].split(' ', 1)[0] if x[11] else '' for x in rows)
    print('types.tsv: %d rows (%d regex rows) -> %s' % (len(rows), nre, os.path.relpath(OUT, S.REPO)))
    print('  shared:', dict(sorted(st.items())))
    print('  lifetime_candidates non-empty:', sum(1 for x in rows if x[9]))
    print('  lifetime_class set:', collections.Counter(x[10].split(' ', 1)[0] for x in rows if x[10]))
    print('  isa_set non-empty:', sum(1 for x in rows if x[8]), 'hierarchies:',
          ', '.join('%s(%d)' % (lab, 1 + len(m.derived(rt))) for rt, lab in hier))
    print('  traits:', sum(1 for x in rows if x[5]), 'structs:', sum(1 for x in rows if x[4]))
    return len(rows)


if __name__ == '__main__':
    main()
