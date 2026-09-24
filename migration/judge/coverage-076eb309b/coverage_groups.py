import json, sys, os, fnmatch
SRC = '/Users/colin/seekdb-dev/cov-076eb309b/src/'
GROUPS = {
  'core: foundation substrate': ['oblib/lib/%s/' % d for d in ('alloc','allocator','container','hash','string','rc','lock','atomic','list','queue')],
  'core: runtime': ['oblib/lib/thread/', 'oblib/lib/utility/', 'share/io/', 'oblib/lib/file/', 'oblib/lib/restore/', 'share/cache/', 'storage/scheduler/', 'data_plane/api/data_plane/scheduler/'],
  'core: value and datum types': ['oblib/common/object/', 'oblib/common/datum/', 'share/datum/', 'share/rc/'],
  'core: statement IR': ['sql/resolver/expr/', 'sql/resolver/dml/*stmt*'],
  'core: execution framework': ['query/api/query/engine/', 'sql/engine/ob_operator*', 'sql/engine/ob_exec_context*', 'sql/engine/ob_physical_plan*', 'sql/engine/expr/ob_expr_frame_info*', 'sql/engine/basic/ob_pushdown_filter*', 'sql/code_generator/'],
  'core: storage and transaction core': ['storage/tablet/', 'storage/meta_mem/', 'storage/memtable/', 'storage/tx/', 'storage/multi_data_source/', 'storage/tx_table/', 'data_plane/api/data_plane/access/'],
  'sql tier': ['sql/optimizer/', 'sql/rewrite/', 'sql/resolver/', 'sql/engine/', 'sql/das/'],
}
def match(rel, pats):
    for p in pats:
        if '*' in p:
            if fnmatch.fnmatch(rel, p): return True
        elif rel.startswith(p): return True
    return False
def agg(files):
    fc=fv=lc=lv=0
    for f in files:
        s=f['summary']; fc+=s['functions']['count']; fv+=s['functions']['covered']; lc+=s['lines']['count']; lv+=s['lines']['covered']
    return fc,fv,lc,lv
def fmt(name, files):
    fc,fv,lc,lv=agg(files)
    return '%-40s files=%5d  functions %7d/%7d = %5.1f%%   lines %8d/%8d = %5.1f%%' % (name, len(files), fv, fc, 100.0*fv/max(fc,1), lv, lc, 100.0*lv/max(lc,1))
d=json.load(open(sys.argv[1]))['data'][0]['files']
src=[f for f in d if f['filename'].startswith(SRC)]
for f in src: f['rel']=f['filename'][len(SRC):]
print(fmt('ALL files in llvm-cov report', d))
print(fmt('all of src/', src))
tops={}
for f in src: tops.setdefault(f['rel'].split('/')[0], []).append(f)
for k in sorted(tops): print(fmt('  src/'+k, tops[k]))
core=set(); sqlt=set()
for g,pats in GROUPS.items():
    fs=[f for f in src if match(f['rel'],pats)]
    print(fmt(g, fs))
    for f in fs: (sqlt if g=='sql tier' else core).add(f['rel'])
byrel={f['rel']:f for f in src}
print(fmt('CORE (union of core rows)', [byrel[r] for r in core]))
print(fmt('SQL TIER', [byrel[r] for r in sqlt]))
print(fmt('CORE + SQL TIER (union)', [byrel[r] for r in core|sqlt]))
