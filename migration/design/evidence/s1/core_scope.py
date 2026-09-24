#!/usr/bin/env python3
"""Hand-written live lines and map units in the core scope of ARCHITECTURE.md 1.3.
Rows use coverage_groups.py's patterns for the report's six narrow rows."""
import collections, fnmatch, json, os, sys
sys.path.insert(0, '/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/01-crates')
from incgraph import category
g = json.load(open('out/graph.json')); lines = g['lines']; live = set(g['live'])
ROWS = [
 ('foundation substrate', ['oblib/lib/%s/' % d for d in ('alloc','allocator','container','hash','string','rc','lock','atomic','list','queue')]),
 ('runtime', ['oblib/lib/thread/', 'oblib/lib/utility/', 'share/io/', 'oblib/lib/file/', 'oblib/lib/restore/', 'share/cache/', 'storage/scheduler/', 'data_plane/api/data_plane/scheduler/']),
 ('value and datum types', ['oblib/common/object/', 'oblib/common/datum/', 'share/datum/', 'share/rc/']),
 ('statement IR', ['sql/resolver/expr/', 'sql/resolver/dml/*stmt*']),
 ('execution framework', ['query/api/query/engine/', 'sql/engine/ob_operator*', 'sql/engine/ob_exec_context*', 'sql/engine/ob_physical_plan*', 'sql/engine/expr/ob_expr_frame_info*', 'sql/engine/basic/ob_pushdown_filter*', 'sql/code_generator/']),
 ('storage and transaction core', ['storage/tablet/', 'storage/meta_mem/', 'storage/memtable/', 'storage/tx/', 'storage/multi_data_source/', 'storage/tx_table/', 'data_plane/api/data_plane/access/']),
 ('schema object model', ['share/schema/', 'observer/schema/']),
 ('storage entry points', ['storage/access/', 'storage/tx_storage/', 'storage/ls/']),
 ('core build: sstable formats', ['storage/blocksstable/']),
 ('core build: WAL (logservice)', ['logservice/']),
 ('core build: recovery (slog, slog_ckpt, checkpoint, meta_store)', ['storage/slog/', 'storage/slog_ckpt/', 'storage/checkpoint/', 'storage/meta_store/']),
]
def m(rel, pats):
    return any(fnmatch.fnmatch(rel, p) if '*' in p else rel.startswith(p) for p in pats)
seen = set(); tot = 0
for name, pats in ROWS:
    fs = [f for f in live if f.startswith('src/') and category(f) == 'hand' and m(f[4:], pats) and f not in seen]
    n = sum(lines[f] for f in fs); tot += n; seen |= set(fs)
    print(f'{name:62} files {len(fs):5}  lines {n:8}  running total {tot:8}')
units = [l.rstrip('\n').split('\t') for l in open('/Users/colin/seekdb-dev/migrate-to-rust/migration/depmap/units.tsv')][1:]
cu = [u for u, files, *_ , ib in units if ib == 'yes' and any(f in seen for f in files.split(','))]
print('in-build map units touching the core scope', len(cu))
