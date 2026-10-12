#!/usr/bin/env python3
"""Draft migration/design/units.tsv (RULEBOOK §4, D14) from the depmap header groups.

Columns: unit  members  target  crate  disposition  disposition_basis  batch
  unit         representative source path (header first; for merged units the declaration-side header)
  members      every member file, comma-separated, sorted (cross-directory merges included)
  target       rust/port/<crate>/src/... per RULEBOOK §4 (exceptions table, `_m` suffix, storage_core, ob_main)
  crate        <crate> of the target
  disposition  translate | replace:<module> | rewrite:<module> | keep-c | keep-cxx:<shim> | generate:<gen> |
               data:<conversion> | delete — assigned only by the RULES table below (first match wins)
  disposition_basis  the rule id; ids ending in -REVIEW mark rows a human/agent must confirm
  batch        translate rows only: floor(index / 100) + 1 over the translate rows in merged translation order

Merging: header groups whose targets coincide become one unit (src/pl/X + src/sql/pl/X;
src/query/api/query/<rest> + src/query/<rest>). The unit graph is contracted accordingly and the
order recomputed the way depmap_seekdb.py does it (Tarjan SCCs, dependencies first; inside an SCC by
earliest file position in migration/depmap/order.txt), as RULEBOOK §4 requires after merging.
Extra rows: RULEBOOK-named files the depmap excluded (ob_uctype.h, data table, excluded as generated).

Also writes migration/inventory/sweep/units-review.tsv (rows with a -REVIEW basis or a non-translate
disposition). Exits non-zero on duplicate targets or an unknown member file.
Usage: python3 -I migration/scripts/inventory/make_units.py
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

REPO = L.REPO
DEPMAP = os.path.join(REPO, 'migration', 'depmap')
OUT = os.path.join(REPO, 'migration', 'design', 'units.tsv')
REVIEW_OUT = os.path.join(REPO, 'migration', 'inventory', 'sweep', 'units-review.tsv')

RUST_KEYWORDS = set('''as async await break const continue crate dyn else enum extern false fn for if impl in let loop
match mod move mut pub ref return self Self static struct super trait true type unsafe use where while abstract become
box do final macro override priv try typeof unsized virtual yield lib main'''.split())

PREFIX_RULES = (  # RULEBOOK §4 exceptions table, then the default src/<module>/<rest>
    ('src/pl/', 'sql', 'pl/'),
    ('src/objit/include/objit/', 'query', 'objit/'),
    ('src/data_plane/api/data_plane/', 'data_plane', ''),
    ('src/query/api/query/', 'query', ''),
)
DECL_SIDE = ('src/sql/pl/', 'src/query/api/query/', 'src/data_plane/api/data_plane/')

STORAGE_CORE = {  # RULEBOOK §4 list (0.9): -> rust/port/storage_core/src/<stem>.rs
    'src/storage/memtable/mvcc/ob_mvcc_row', 'src/storage/memtable/mvcc/ob_row_latch',
    'src/storage/memtable/mvcc/ob_keybtree', 'src/storage/memtable/mvcc/ob_keybtree_deps',
    'src/storage/memtable/mvcc/ob_tx_callback_list', 'src/storage/memtable/mvcc/ob_mvcc',
    'src/storage/memtable/ob_memtable_data', 'src/storage/allocator/ob_memstore_allocator',
    'src/storage/allocator/ob_fifo_arena', 'src/logservice/palf/log_group_buffer', 'src/logservice/palf/lsn_allocator',
}
DEAD_UNITS = {  # RULEBOOK 0.8 不翻译的死代码 (evidence recomputed by sweep_c16_dead.py)
    'src/oblib/lib/lock/ob_bucket_qsync_lock', 'src/oblib/lib/allocator/ob_delay_free_allocator',
    'src/oblib/lib/thread/ob_map_queue_thread_pool', 'src/oblib/lib/queue/ob_ms_queue',
    'src/oblib/lib/queue/ob_co_seq_queue', 'src/oblib/lib/container/ob_ring_array',
}
ITEM_TYPE_UNITS = {'src/objit/include/objit/common/ob_item_type', 'src/query/api/query/parser/ob_item_type',
                   'src/sql/parser/ob_item_type'}
INFRA_DIRS = {  # RULEBOOK §4 "oblib 基础设施目录" -> facade module of the §4 rename table / 0.2 list
    'lock': 'sync', 'atomic': 'atomic', 'hash': 'hash', 'queue': 'queue', 'container': 'container',
    'list': 'container', 'allocator': 'mem', 'alloc': 'mem', 'objectpool': 'mem', 'thread': 'thread',
    'thread_local': 'thread', 'oblog': 'log', 'signal': 'signal', 'task': 'thread',
}
INFRA_NAMED = {  # oblib units RULEBOOK names explicitly, with the module that replaces them
    'src/oblib/lib/utility/ob_smart_call': ('replace:thread', 'R-infra-named:smart_call'),
    'src/oblib/lib/allocator/ob_hazard_ref': ('replace:hash', 'R-infra-named:hazard_ref'),
    'src/oblib/lib/allocator/ob_qsync': ('replace:sync', 'R-infra-named:qsync'),
    'src/oblib/lib/allocator/ob_retire_station': ('rewrite:storage_core', 'R-infra-named:retire_station-REVIEW'),
    'src/oblib/lib/utility/ob_sort': ('replace:sort', 'R-infra-named:ob_sort'),
    'src/oblib/lib/utility/serialization': ('replace:serialization', 'R-infra-named:serialization'),
    'src/oblib/lib/utility/ob_unify_serialize': ('replace:serialization', 'R-infra-named:serialization'),
    'src/oblib/lib/utility/ob_serialization_helper': ('replace:serialization', 'R-infra-named:serialization'),
    'src/oblib/lib/utility/ob_print_utils': ('replace:fmt', 'R-infra-named:print_utils'),
    'src/oblib/lib/utility/ob_print_kv': ('replace:fmt', 'R-infra-named:print_kv'),
    'src/oblib/lib/utility/ob_macro_utils': ('replace:prelude', 'R-infra-named:macro_utils-REVIEW'),
    'src/oblib/lib/rc/context': ('replace:mem', 'R-infra-named:memory_context'),
    'src/oblib/lib/resource/achunk_mgr': ('replace:mem', 'R-infra-named:achunk_mgr-REVIEW'),
    'src/oblib/lib/string/ob_string': ('replace:string', 'R-infra-named:ob_string'),
    'src/oblib/lib/string/ob_sql_string': ('replace:string', 'R-infra-named:ob_sql_string'),
    'src/oblib/lib/string/ob_string_buffer': ('replace:string', 'R-infra-named:ob_string_buffer'),
}
SHARE_CACHE = {'src/share/cache/ob_kvcache_hazard_domain', 'src/share/cache/ob_kvcache_hazard_pointer',
               'src/share/cache/ob_kvcache_hazard_version', 'src/share/cache/ob_kvcache_map'}
SHARE_CACHE_ADJ = {'src/share/cache/ob_kvcache_struct', 'src/share/cache/ob_kvcache_store'}
BGK = {'src/share/geo/ob_geo_bin_traits', 'src/share/geo/ob_geo_tree_traits', 'src/share/geo/ob_geo_tree'}
BGK_ADJ = {'src/share/geo/ob_geo_bin', 'src/share/geo/ob_geo_bin_iter'}
FRAME = {'src/query/api/query/engine/expr/ob_expr', 'src/sql/engine/expr/ob_expr_frame_info'}
FRAME_ADJ = {'src/sql/engine/expr/ob_expr'}
DATA_SPLIT = {'src/storage/fts/dict/ob_ik_dic', 'src/oblib/common/timezone/ob_timezone_info'}
UNCLEAR_INFRA = re.compile(r'^src/oblib/lib/(coro|core_local)/|^src/oblib/lib/stat/ob_latch_define$')
# C++ libraries RULEBOOK 0.14 keeps behind a cxx shim (or replaces, gRPC): the shim/translation boundary of a
# unit that calls them directly has to be confirmed. Regexes follow the parser memo's api_sites.py ([C6]).
SHIM_APIS = (
    ('boost_geometry', 'bg::', re.compile(r'\bboost::geometry::|\bbg::|\bbgm::|\bbgi::|\bbgd::|\bbgsr::|\bbg_srs::')),
    ('boost_spirit', 'qi::', re.compile(r'\bqi::|\bphoenix::|\bboost::spirit')),
    ('s2', 'S2', re.compile(r'\bS2(Cell|CellId|CellUnion|LatLng|LatLngRect|Polygon|Polyline|Loop|Point|Cap|RegionCoverer|'
                            r'Region|Earth|Shape\w*|Builder\w*|BooleanOperation|LatLngRectBounder)\b|\bS1(Angle|ChordAngle|'
                            r'Interval)\b')),
    ('vsag', 'vsag::', re.compile(r'\bvsag::')),
    ('std_regex', 'regex', re.compile(r'\bstd::(regex|wregex|smatch|cmatch|regex_\w+)\b')),
    ('grpc', 'grpc', re.compile(r'\bgrpc::|\bgoogle::protobuf::')),
)
EXTRA_ROWS = [  # (path, disposition, basis): RULEBOOK-named files that the depmap excluded
    ('src/oblib/lib/charset/ob_uctype.h', 'data:uctype', 'R-data-excluded'),
]


def read_tsv(path):
    with open(path, encoding='utf-8') as fh:
        head = fh.readline().rstrip('\n').split('\t')
        return head, [ln.rstrip('\n').split('\t') for ln in fh if ln.strip()]


def target_of(unit):
    if unit in STORAGE_CORE:
        return 'rust/port/storage_core/src/%s.rs' % unit.rsplit('/', 1)[1]
    if unit == 'src/observer/main':
        return 'rust/port/observer/src/ob_main.rs'
    for pre, crate, sub in PREFIX_RULES:
        if unit.startswith(pre):
            rest = sub + unit[len(pre):]
            break
    else:
        parts = unit.split('/')
        crate, rest = parts[1], '/'.join(parts[2:])
    comps = [c + '_m' if c in RUST_KEYWORDS else c for c in rest.split('/')]
    return 'rust/port/%s/src/%s.rs' % (crate, '/'.join(comps))


def ext_rank(f):
    ext = os.path.splitext(f)[1]
    return {'.h': 0, '.hpp': 0, '.cpp': 1, '.cc': 1, '.c': 1}.get(ext, 2)


def representative(files):
    return min(files, key=lambda f: (ext_rank(f), 0 if f.startswith(DECL_SIDE) else 1, f))


def tarjan_scc(nodes, edges):
    """Iterative Tarjan as in migration/scripts/depmap_seekdb.py; SCCs come out dependencies first."""
    adj = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)
    for n in adj:
        adj[n].sort()
    counter = [0]
    stack, on_stack = [], set()
    index, low = {}, {}
    sccs = []
    for start in nodes:
        if start in index:
            continue
        work = [(start, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            for i in range(pi, len(adj[node])):
                succ = adj[node][i]
                if succ not in index:
                    work[-1] = (node, i + 1)
                    work.append((succ, 0))
                    recurse = True
                    break
                elif succ in on_stack:
                    low[node] = min(low[node], index[succ])
            if recurse:
                continue
            if low[node] == index[node]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                sccs.append(sorted(scc))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sccs


def resolve_include(src, inc, nodes):
    for cand in (os.path.normpath(os.path.join(os.path.dirname(src), inc)), 'src/' + inc, 'src/oblib/' + inc,
                 'src/query/api/' + inc, 'src/data_plane/api/' + inc, 'src/objit/include/' + inc):
        cand = cand.replace(os.sep, '/')
        if cand in nodes:
            return cand
    return None


def parser_c_closure(nodes, file_edges):
    """Files reachable through #include from the parser C sources (.c nodes and tracked .y/.l grammars)."""
    seeds = set()
    for d in ('src/sql/parser', 'src/pl/parser'):
        for f in sorted(os.listdir(os.path.join(REPO, d))):
            p = d + '/' + f
            if p in nodes and f.endswith('.c'):
                seeds.add(p)
            elif f.endswith(('.y', '.l')):
                with open(os.path.join(REPO, p), encoding='utf-8', errors='replace') as fh:
                    for m in re.finditer(r'(?m)^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', fh.read()):
                        r = resolve_include(p, m.group(1), nodes)
                        if r:
                            seeds.add(r)
    seen, todo = set(seeds), sorted(seeds)
    while todo:
        x = todo.pop()
        for y in file_edges.get(x, ()):
            if y not in seen:
                seen.add(y)
                todo.append(y)
    return seen


def main():
    _, unit_rows = read_tsv(os.path.join(DEPMAP, 'units', 'units.tsv'))
    _, node_rows = read_tsv(os.path.join(DEPMAP, 'nodes.tsv'))
    nodes = {r[0] for r in node_rows}
    unit_files = {}
    for u, files, _lines, _crate in unit_rows:
        d = u.rsplit('/', 1)[0]
        unit_files[u] = sorted(d + '/' + f for f in files.split(','))
        for f in unit_files[u]:
            if f not in nodes:
                sys.exit('make_units: unknown member %s of %s' % (f, u))
    # ---- merge by target
    groups = collections.defaultdict(list)
    for u in sorted(unit_files):
        groups[target_of(u)].append(u)
    key_of = {}
    merged = {}
    for tgt, us in groups.items():
        k = us[0]  # sorted -> minimal depmap unit id
        merged[k] = {'units': us, 'target': tgt, 'files': sorted(f for u in us for f in unit_files[u])}
        for u in us:
            key_of[u] = k
    # ---- order: contracted graph, depmap method
    _, uedges = read_tsv(os.path.join(DEPMAP, 'units', 'edges.tsv'))
    edges = sorted({(key_of[a], key_of[b]) for a, b, _w in uedges if key_of[a] != key_of[b]})
    file_pos = {}
    with open(os.path.join(DEPMAP, 'order.txt'), encoding='utf-8') as fh:
        i = 0
        for ln in fh:
            if ln.startswith('#') or not ln.strip():
                continue
            for f in ln.rstrip('\n').split('\t'):
                file_pos[f] = i
            i += 1
    keys = sorted(merged)
    sccs = tarjan_scc(keys, edges)
    sccs = [sorted(s, key=lambda k: (min(file_pos[f] for f in merged[k]['files']), k)) for s in sccs]
    order = [k for s in sccs for k in s]
    # ---- parser C closure
    _, fedges = read_tsv(os.path.join(DEPMAP, 'edges.tsv'))
    file_edges = collections.defaultdict(set)
    for r in fedges:
        file_edges[r[0]].add(r[1])
    c_side = parser_c_closure(nodes, file_edges)

    def disposition(k):
        m = merged[k]
        us, files = m['units'], m['files']
        exts = {os.path.splitext(f)[1] for f in files}
        uset = set(us)
        if uset & DEAD_UNITS:
            return 'delete', 'R-dead'
        if uset & ITEM_TYPE_UNITS:
            return 'generate:item_type', 'R-itemtype'
        if any(f.endswith('/ob_errno.def') for f in files):
            return 'generate:errno', 'R-errno-def'
        if all(f.startswith('src/oblib/easy/') for f in files):
            return 'delete', 'R-libeasy-remnant'
        if exts <= {'.map'}:
            return 'data:map_table', 'R-data-map'
        if exts <= {'.def'}:
            return 'data:def_table', 'R-data-def'
        if any(f.endswith('.def') for f in files) or uset & DATA_SPLIT:
            return 'translate', 'R-data-split-REVIEW'
        if all(re.match(r'src/oblib/lib/charset/\w+_tab\.h$', f) for f in files):
            return 'data:charset_table', 'R-data-charset-tab'
        if any(L.in_parser_dir(f) for f in files):
            if any(f.endswith('.c') for f in files):
                return 'keep-c', 'R-parser-c'
            if exts <= {'.h'} and any(f in c_side for f in files):
                return 'keep-c', 'R-parser-c-header'
            return 'translate', 'R-parser-cxx-REVIEW'
        if uset & STORAGE_CORE:
            return 'rewrite:storage_core', 'R-storage-core'
        if uset & SHARE_CACHE:
            return 'rewrite:share_cache', 'R-share-cache'
        if uset & SHARE_CACHE_ADJ:
            return 'translate', 'R-share-cache-adjacent-REVIEW'
        if 'src/oblib/common/datum/ob_datum' in uset:
            return 'rewrite:datum', 'R-datum'
        if 'src/oblib/common/object/ob_object' in uset:
            return 'rewrite:object', 'R-object'
        if uset & FRAME:
            return 'rewrite:frame', 'R-frame'
        if uset & FRAME_ADJ:
            return 'translate', 'R-frame-adjacent-REVIEW'
        if uset & BGK:
            return 'keep-cxx:bgk_shim', 'R-bgk'
        if uset & BGK_ADJ:
            return 'translate', 'R-bgk-adjacent-REVIEW'
        for u in us:
            if u in INFRA_NAMED:
                return INFRA_NAMED[u]
        for u in us:
            mm = re.match(r'src/oblib/lib/(\w+)/', u)
            if mm and mm.group(1) in INFRA_DIRS:
                return 'replace:' + INFRA_DIRS[mm.group(1)], 'R-infra-dir:%s' % mm.group(1)
        if any(UNCLEAR_INFRA.match(u) for u in us):
            return 'translate', 'R-default-REVIEW'
        if any(re.search(r'_tab$', u) for u in us):
            return 'translate', 'R-data-candidate-REVIEW'
        libs = sorted({name for f in files if f.endswith(L.SOURCE_EXT) for name, pre, rx in SHIM_APIS
                       if pre in L.raw(f) and rx.search(L.stripped(f))})
        if libs:
            return 'translate', 'R-thirdparty-shim:%s-REVIEW' % '+'.join(libs)
        return 'translate', 'R-default'

    rows = []
    targets = collections.Counter()
    for k in order:
        m = merged[k]
        disp, basis = disposition(k)
        rows.append([representative(m['files']), ','.join(m['files']), m['target'],
                     m['target'].split('/')[2], disp, basis, ''])
        targets[m['target']] += 1
    for path, disp, basis in EXTRA_ROWS:
        u = os.path.splitext(path)[0]
        tgt = target_of(u)
        rows.append([path, path, tgt, tgt.split('/')[2], disp, basis, ''])
        targets[tgt] += 1
    dups = [t for t, c in targets.items() if c > 1]
    if dups:
        sys.exit('make_units: duplicate targets: %s' % ', '.join(sorted(dups)[:10]))
    idx = 0
    for r in rows:
        if r[4] == 'translate':
            r[6] = str(idx // 100 + 1)
            idx += 1
    header = ['unit', 'members', 'target', 'crate', 'disposition', 'disposition_basis', 'batch']
    L.write_tsv(OUT, header, rows)
    review = [r for r in rows if r[5].endswith('-REVIEW') or r[4] != 'translate']
    L.write_tsv(REVIEW_OUT, header, review)
    # ---- report
    print('wrote %s: %d units (%d depmap header groups, %d merges, %d extra rows)' % (
        L.rel(OUT), len(rows), len(unit_files), sum(len(m['units']) - 1 for m in merged.values()), len(EXTRA_ROWS)))
    print('wrote %s: %d rows' % (L.rel(REVIEW_OUT), len(review)))
    by_disp = collections.Counter(r[4] for r in rows)
    print('by disposition:', ', '.join('%s %d' % kv for kv in sorted(by_disp.items())))
    by_crate = collections.Counter(r[3] for r in rows)
    print('by crate:', ', '.join('%s %d' % kv for kv in sorted(by_crate.items())))
    print('REVIEW rows:', sum(1 for r in rows if r[5].endswith('-REVIEW')))
    print('translate batches:', (idx + 99) // 100)
    by_basis = collections.Counter(r[5] for r in rows)
    for b, c in sorted(by_basis.items()):
        print('  %-90s %d' % (b, c))


if __name__ == '__main__':
    main()
