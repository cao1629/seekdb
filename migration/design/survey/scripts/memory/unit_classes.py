# Rough split of manifest units by expected dominant memory lifetime (directory heuristic).
# Usage: python3 -I unit_classes.py <manifest.tsv>
import sys, collections
rows = [l.rstrip('\n').split('\t') for l in open(sys.argv[1]) if not l.startswith('source\t')]
RULES = [
    ('oblib allocator/container library (rewrite, not translate)', ('src/oblib/lib/alloc/', 'src/oblib/lib/allocator/', 'src/oblib/lib/container/', 'src/oblib/lib/hash/', 'src/oblib/lib/rc/', 'src/oblib/lib/resource/', 'src/oblib/lib/objectpool/', 'src/oblib/lib/queue/', 'src/oblib/lib/list/')),
    ('query-scoped: parse/resolve/rewrite/optimize/codegen', ('src/sql/parser/', 'src/sql/resolver/', 'src/sql/rewrite/', 'src/sql/optimizer/', 'src/sql/code_generator/', 'src/sql/printer/')),
    ('query/batch-scoped: execution engine & DAS & PL runtime', ('src/sql/engine/', 'src/sql/das/', 'src/sql/dtl/', 'src/sql/executor/', 'src/query/', 'src/pl/', 'src/sql/pl/', 'src/sql/ob_')),
    ('long-lived owned: plan cache / session / schema', ('src/sql/plan_cache/', 'src/sql/session/', 'src/share/schema/')),
    ('storage (mixed: per-scan arenas, memtable regions, meta pools)', ('src/storage/',)),
    ('other sql (monitor, udr, ...)', ('src/sql/',)),
    ('server/share/rootserver/logservice/... (mostly owned)', ('src/',)),
]
c = collections.Counter()
for src, _ in rows:
    for name, prefixes in RULES:
        if src.startswith(prefixes):
            c[name] += 1
            break
total = sum(c.values())
for name, _ in RULES:
    print('%5d  %s' % (c[name], name))
print('%5d  total' % total)
