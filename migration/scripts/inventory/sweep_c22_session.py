#!/usr/bin/env python3
"""c22-session: every field of the session (RULEBOOK 0.5 会话, §2.7 重入, §5.2 #22).

Sites: every data member of ObSQLSessionInfo and ObBasicSessionInfo ('session field') and of the records
declared in the same two headers (src/sql/session/ob_basic_session_info.h, ob_sql_session_info.h) that they
hold by value, by pointer, as array/container element, or as a base of such a record, transitively
('session member-struct field').  Anonymous struct/union members are flattened into their record.

symbol: '<Class chain>::<field>' (e.g. 'ObBasicSessionInfo::MultiThreadData::state_').

pretag hints:
  cross-thread             the field, or a small accessor of it (a session method whose body references at
                           most 3 session fields; constructors, destructors and generic names such as reset,
                           to_string, serialize, assign are not accessors), is referenced from a cross-thread
                           path: src/sql/session/
                           ob_sql_session_mgr.*, src/observer/virtual_table/ob_show_processlist.*,
                           src/observer/virtual_table/ob_all_virtual_*session*, src/sql/engine/cmd/ob_kill_*,
                           and the functions with 'kill' in their name in src/sql/ob_sql_trans_control.*
  under-query-mutex        referenced in a session method, or a cross-thread function, that takes
                           query_mutex_ (get_query_lock(), try_lock_query(), LockGuard on query_mutex_)
  under-thread-data-mutex  same for thread_data_mutex_ (get_thread_data_lock(), LockGuard on thread_data_mutex_)
  atomic                   accessed with ATOMIC_* in the session or cross-thread files, or of std::atomic/
                           volatile type
  in-thread-data           a field of MultiThreadData (the thread_data_ struct guarded by thread_data_mutex_)
  lock                     the field is itself a lock (query_mutex_, thread_data_mutex_)
details: type=<type>, accessors=<methods> (at most 4), xt=<file:line> (first cross-thread reference).
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3alib as S  # noqa: E402
import sweeplib as L  # noqa: E402

CAT = 'c22-session'
HEADERS = ('src/sql/session/ob_basic_session_info.h', 'src/sql/session/ob_sql_session_info.h')
SESSION_FILES = ('src/sql/session/ob_basic_session_info.h', 'src/sql/session/ob_basic_session_info.cpp',
                 'src/sql/session/ob_sql_session_info.h', 'src/sql/session/ob_sql_session_info.cpp')
XT_GLOBS = (r'^src/sql/session/ob_sql_session_mgr\.', r'^src/observer/virtual_table/ob_show_processlist\.',
            r'^src/observer/virtual_table/ob_all_virtual_[^/]*session[^/]*\.', r'^src/sql/engine/cmd/ob_kill_[^/]*\.')
XT_KILL_ONLY = (r'^src/sql/ob_sql_trans_control\.',)
RX_QLOCK = re.compile(r'\b(?:query_mutex_|get_query_lock\s*\(|try_lock_query\s*\()')
RX_DLOCK = re.compile(r'\b(?:thread_data_mutex_|get_thread_data_lock\s*\(|try_lock_thread_data\s*\()')
RX_ATOMIC = re.compile(r'\bATOMIC_\w+\s*\(\s*&?\s*\(?\s*(?:this\s*->\s*)?((?:[A-Za-z_]\w*\s*(?:\.|->)\s*)*)'
                       r'([A-Za-z_]\w*)')
RX_CALL = re.compile(r'(?:\.|->)\s*([A-Za-z_]\w*)\s*\(')
RX_FIELDREF = re.compile(r'(?:\.|->)\s*([A-Za-z_]\w*)\b(?!\s*\()')
RX_IDENT = re.compile(r'\b[A-Za-z_]\w*\b')
GENERIC = frozenset('reset reuse destroy init clear assign to_string serialize deserialize serialize_ deserialize_ '
                    'get_serialize_size get_serialize_size_ hash is_valid operator= operator== operator!= '
                    'operator< swap print'.split())


def session_records(m):
    roots = [u for q in S.SESSION_ROOTS for u in [m.resolve(q)] if u]
    seen, todo = [], list(roots)
    while todo:
        u = todo.pop(0)
        if u in seen:
            continue
        seen.append(u)
        nxt = []
        for a in m.ancestors(u):
            if a in m.defs and m.defs[a]['file'] in HEADERS:
                nxt.append(a)
        for f, es in m.member_edges(u):
            for k, t, h in es:
                if t in m.defs and m.defs[t]['file'] in HEADERS:
                    nxt.append(t)
        for v in nxt:
            if v not in seen:
                todo.append(v)
    return roots, seen


def func_defs_in(m, path):
    out = []
    for r in m.db.records:
        if r['kind'] in S.FK and r.get('is_definition') and r['file'] == path and 'end_line' in r:
            out.append(r)
    out.sort(key=lambda r: (r['line'], r['usr']))
    return out


def main():
    m = S.Model(funcs=True)
    roots, recs = session_records(m)
    recset = set(recs)
    # field name -> [(record usr, field record)]
    by_name = collections.defaultdict(list)
    own = {}
    for u in recs:
        fs = m.fields(u)
        own[u] = fs
        for f in fs:
            by_name[f['name']].append((u, f))
    inherited_names = {}
    for u in recs:
        names = set(f['name'] for f in own[u])
        for a in m.ancestors(u):
            if a in recset:
                names.update(f['name'] for f in own[a])
        inherited_names[u] = names
    # session methods: which fields each body references; lock usage
    refs_by_method = {}
    method_lock = {}
    for u in recs:
        for d in m.func_records(u, S.FK):
            body = m.body(d)
            if not body:
                continue
            idents = set(RX_IDENT.findall(body))
            hit = set()
            for n in idents & set(by_name):
                for (ru, f) in by_name[n]:
                    if ru == u or n in inherited_names[u] and ru in [u] + list(m.ancestors(u)):
                        hit.add((ru, n))
                if not any(x[1] == n for x in hit):
                    for (ru, f) in by_name[n]:
                        if re.search(r'(?:\.|->)\s*%s\b' % re.escape(n), body):
                            hit.add((ru, n))
            refs_by_method[(u, d['name'], d['usr'])] = hit
            method_lock[(u, d['name'], d['usr'])] = (bool(RX_QLOCK.search(body)), bool(RX_DLOCK.search(body)))
    accessors = collections.defaultdict(set)
    under_q, under_d = set(), set()
    for key, hit in refs_by_method.items():
        u, name, usr = key
        ql, dl = method_lock[key]
        if name in ('get_query_lock', 'get_thread_data_lock', 'try_lock_query', 'try_lock_thread_data',
                    'unlock_query', 'unlock_thread_data'):
            continue
        if 0 < len(hit) <= 3 and name not in GENERIC and name != m.defs[u]['name'] and not name.startswith('~'):
            for h in hit:
                accessors[h].add(name)
        for h in hit:
            if ql:
                under_q.add(h)
            if dl:
                under_d.add(h)
    acc_to_fields = collections.defaultdict(set)
    for h, names in accessors.items():
        for n in names:
            acc_to_fields[n].add(h)
    # cross-thread files
    xt = {}
    xt_q, xt_d = set(), set()
    files = L.first_party()
    for p in files:
        full = any(re.match(g, p) for g in XT_GLOBS)
        kill = any(re.match(g, p) for g in XT_KILL_ONLY)
        if not (full or kill):
            continue
        texts = []
        if full:
            fdefs = func_defs_in(m, p)
            covered = set()
            for d in fdefs:
                texts.append((d['line'], m.body(d)))
                covered.update(range(d['line'], d['end_line'] + 1))
            rest = [ln if (i + 1) not in covered else '' for i, ln in enumerate(L.stripped_lines(p))]
            texts.append((1, '\n'.join(rest)))
        else:
            for d in func_defs_in(m, p):
                if 'kill' in d['name'].lower():
                    texts.append((d['line'], m.body(d)))
        for base_line, body in texts:
            ql = bool(RX_QLOCK.search(body))
            dl = bool(RX_DLOCK.search(body))
            for mm in RX_CALL.finditer(body):
                for h in acc_to_fields.get(mm.group(1), ()):
                    line = base_line + body.count('\n', 0, mm.start())
                    xt.setdefault(h, '%s:%d' % (p, line))
                    if ql:
                        xt_q.add(h)
                    if dl:
                        xt_d.add(h)
            for mm in RX_FIELDREF.finditer(body):
                for (ru, f) in by_name.get(mm.group(1), ()):
                    h = (ru, mm.group(1))
                    line = base_line + body.count('\n', 0, mm.start())
                    xt.setdefault(h, '%s:%d' % (p, line))
                    if ql:
                        xt_q.add(h)
                    if dl:
                        xt_d.add(h)
    # ATOMIC_* uses
    atomic = set()
    for p in sorted(set(SESSION_FILES) | set(f for f in files if any(re.match(g, f) for g in XT_GLOBS + XT_KILL_ONLY))):
        if not os.path.exists(os.path.join(S.REPO, p)):
            continue
        for mm in RX_ATOMIC.finditer(L.stripped(p)):
            n = mm.group(2)
            for (ru, f) in by_name.get(n, ()):
                atomic.add((ru, n))
    multi = m.resolve('oceanbase::sql::ObBasicSessionInfo::MultiThreadData')
    rows = []
    for u in recs:
        cons = 'session field' if u in roots else 'session member-struct field'
        for f in own[u]:
            h = (u, f['name'])
            tags, det = [], []
            if h in xt:
                tags.append('cross-thread')
            if h in under_q or h in xt_q:
                tags.append('under-query-mutex')
            if h in under_d or h in xt_d:
                tags.append('under-thread-data-mutex')
            ct = f.get('canonical_type', '') or ''
            if h in atomic or S.ATOMIC_CANON_RX.search(ct):
                tags.append('atomic')
            if u == multi:
                tags.append('in-thread-data')
            if any(k in ('value', 'infra') and t in m.locks for k, t, hw in m.field_edges(f)):
                tags.append('lock')
            det.append('type=%s' % m.strip_ns(f.get('type', '')))
            acc = sorted(accessors.get(h, ()))
            if acc:
                det.append('accessors=%s' % '+'.join(acc[:4]))
            if h in xt:
                det.append('xt=%s' % xt[h])
            rows.append((f['file'], '%s::%s' % (m.chain(u), f['name']), cons, f['line'], S.fmt_tags(tags, det)))
    by = collections.Counter(r[2] for r in rows)
    tagc = collections.Counter(t for r in rows for t in S.tags_of(r[4]))
    note = ('fields of ObSQLSessionInfo/ObBasicSessionInfo %d + of the %d records of the two session headers they hold '
            '%d; pretags %s') % (by['session field'], len(recs) - len(roots), by['session member-struct field'],
                                 ' '.join('%s=%d' % kv for kv in sorted(tagc.items())))
    n = L.finish(CAT, rows, note)
    for k, v in sorted(by.items()):
        print('  %-30s %d' % (k, v))
    for k, v in sorted(tagc.items()):
        print('  tag %-26s %d' % (k, v))
    print('  records:', ' '.join(m.chain(u) for u in recs))
    return n


if __name__ == '__main__':
    main()
