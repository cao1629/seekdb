#!/usr/bin/env python3
"""Third-party usage depth for seekdb at the migration base.
Usage: python3 -I thirdparty_depth.py REPO FILELIST OUT_TSV
- direct: tracked src files (C/C++/grammar) whose #include names the library
- reach: direct includers plus every node that transitively includes one (migration/depmap/edges.tsv),
         plus files that include an excluded generated wrapper that includes the library (external_refs.tsv)
- units/crates: manifest units (migration/depmap/nodes.tsv 'unit' column) touched by reach
- api_lines: lines matching the library's API regex, counted only in reach files (limits false positives),
             comment lines ('//' or '*' leading) skipped; vendored/generated files excluded.
"""
import os, re, sys, collections

repo, flist, out = sys.argv[1], sys.argv[2], sys.argv[3]
EXT = {'.c', '.h', '.cc', '.cpp', '.hpp', '.ipp', '.y', '.l'}
files = [l.strip() for l in open(flist) if l.strip()]
src = [f for f in files if f.startswith('src/') and os.path.splitext(f)[1] in EXT]

excluded = {}
for line in open(os.path.join(repo, 'migration/depmap/excluded.tsv')):
    p, c, *_ = line.rstrip('\n').split('\t')
    if p != 'path':
        excluded[p] = c

LIBS = [
 # name, include regex, API regex
 ('Boost.Geometry', r'^boost/geometry', r'\bboost::geometry::|\bbg::|\bbgm::|\bbgi::|\bbgd::'),
 ('Boost.Spirit/Phoenix', r'^boost/spirit', r'\bqi::|\bphoenix::|\bboost::spirit'),
 ('ICU', r'^unicode/', r'\bu(regex|col|cnv|brk|loc|text|norm2?|str|set|trans|idna|calendar)_\w+\s*\(|\bu_\w+\s*\(|\bicu::|\bUErrorCode\b|\bURegularExpression\b|\bUText\b|\bU_FAILURE\b|\bU_SUCCESS\b'),
 ('libxml2', r'^libxml2?/|^libxml/', r'\bxml[A-Z]\w*\b|\bhtml[A-Z]\w*\s*\(|\bXML_[A-Z_]+\b'),
 ('libcurl', r'^curl/', r'\bcurl_\w+\s*\(|\bCURL\w*\b'),
 ('OpenSSL', r'^openssl/', r'\b(EVP|SSL|RSA|BIO|HMAC|RAND|ERR|AES|PEM|EC|ECDSA|CRYPTO|OPENSSL|DES|BN|DH|X509|SHA\d*|MD5|ENGINE|OBJ|ASN1|i2d|d2i|PKCS\d*)_\w+\b|\bSSL_CTX\b|\bX509\b'),
 ('zlib', r'^zlib\.h$|^zconf\.h$', r'\b(deflate\w*|inflate\w*|compress2?|uncompress\w*|compressBound|crc32|adler32)\s*\(|\bz_stream\b|\bZ_(OK|STREAM_END|BUF_ERROR|DEFAULT_COMPRESSION)\b'),
 ('zstd 1.3.8 (vendored)', r'^zstd\.h$|zstd_src/|ob_zstd_wrapper\.h$', r'\bZSTD_\w+'),
 ('protobuf C++ / gRPC', r'^google/protobuf/|^grpcpp?/|^grpc/|standbyservice\.(grpc\.)?pb\.h$', r'\bgrpc::|\bgoogle::protobuf::|\b::grpc::'),
 ('protobuf-c', r'^protobuf-c/|ob_vector_tile\.pb-c\.h$', r'\bprotobuf_c_\w+|\b\w+__(pack|unpack|init|free_unpacked|get_packed_size)\s*\('),
 ('s2geometry', r'^s2/', r'\bS2\w*\b|\bS1(Angle|ChordAngle|Interval)\b|\bR[12](Interval|Rect|Point)\b'),
 ('CRoaring', r'^roaring/|^roaring\.h', r'\broaring(64)?_bitmap_\w+|\broaring::|\broaring64_\w+|\broaring_\w+\s*\('),
 ('vsag', r'^vsag/', r'\bvsag::'),
 ('SQLite', r'sqlite3\.h$', r'\bsqlite3_\w+|\bSQLITE_[A-Z_]+\b'),
 ('RapidJSON', r'^rapidjson/', r'\brapidjson::'),
 ('fast_float', r'^fast_float/', r'\bfast_float::'),
 ('libaio', r'^libaio\.h$', r'\bio_(submit|getevents|setup|destroy|cancel|prep_\w+)\b|\bstruct\s+iocb\b|\bio_context_t\b'),
 ('libunwind', r'^libunwind', r'\bunw_\w+'),
 ('jemalloc', r'^jemalloc/|jemalloc\.h$', r'\bje_\w+|\bmallctl\w*\b|\bmallocx\b|\bsdallocx\b'),
 ('tzcode (vendored)', r'timezone/(private|tzfile)\.h$|^tzfile\.h$|^private\.h$', r'\btzalloc\b|\blocaltime_rz\b|\bmktime_z\b'),
 ('xxHash (vendored)', r'xxhash\.h$', r'\bXXH\w+'),
 ('libev header (vendored)', r'^ev\.h$|io/ev\.h$|easy/io/ev', r'\bev_\w+\s*\('),
]

INC = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
txt_cache = {}
def text(f):
    if f not in txt_cache:
        try:
            txt_cache[f] = open(os.path.join(repo, f), encoding='utf-8', errors='replace').read()
        except FileNotFoundError:
            txt_cache[f] = ''
    return txt_cache[f]

# reverse include graph over depmap nodes
rev = collections.defaultdict(set)
for line in open(os.path.join(repo, 'migration/depmap/edges.tsv')):
    a, b, *_ = line.rstrip('\n').split('\t')
    if a == 'from':
        continue
    rev[b].add(a)
# external refs: node -> excluded file (e.g. generated .pb.h)
ext_rev = collections.defaultdict(set)
for line in open(os.path.join(repo, 'migration/depmap/external_refs.tsv')):
    parts = line.rstrip('\n').split('\t')
    if parts[0] == 'from':
        continue
    ext_rev[parts[2]].add(parts[0])
unit_of, crate_of = {}, {}
for line in open(os.path.join(repo, 'migration/depmap/nodes.tsv')):
    p, n, crate, unit = line.rstrip('\n').split('\t')
    if p == 'path':
        continue
    unit_of[p] = unit; crate_of[p] = crate

def closure(seeds):
    seen = set(seeds); stack = list(seeds)
    while stack:
        x = stack.pop()
        for y in rev.get(x, ()):
            if y not in seen:
                seen.add(y); stack.append(y)
    return seen

rows = []
for name, inc_rx, api_rx in LIBS:
    irx, arx = re.compile(inc_rx), re.compile(api_rx)
    direct = set()
    for f in src:
        for inc in INC.findall(text(f)):
            if irx.search(inc):
                direct.add(f); break
    direct_nodes = {f for f in direct if f not in excluded}
    seeds = set(direct_nodes)
    for f in direct:
        if f in excluded:            # e.g. generated .pb.h / vendored header: who includes it?
            seeds |= ext_rev.get(f, set())
    reach = closure(seeds)
    reach_nodes = {f for f in reach if f in unit_of}
    units = {unit_of[f] for f in reach_nodes}
    crates = collections.Counter(crate_of[f].replace('src/', '') for f in reach_nodes)
    api_lines = 0; api_files = set()
    for f in reach_nodes | direct_nodes:
        for line in text(f).splitlines():
            s = line.strip()
            if s.startswith('//') or s.startswith('*') or s.startswith('/*') or s.startswith('#include'):
                continue
            if arx.search(line):
                api_lines += 1; api_files.add(f)
    rows.append((name, len(direct), len(direct_nodes), len(reach_nodes), len(units), api_lines, len(api_files),
                 ','.join(f'{k}:{v}' for k, v in crates.most_common(6)), ' '.join(sorted(direct)[:6])))

with open(out, 'w') as fh:
    fh.write('library\tdirect_files\tdirect_nodes\treach_files\treach_units\tapi_lines\tapi_files\treach_by_crate\tdirect_examples\n')
    for r in rows:
        fh.write('\t'.join(map(str, r)) + '\n')
for r in rows:
    print(f'{r[0]:26s} direct={r[1]:3d} reach={r[3]:5d} units={r[4]:5d} api_lines={r[5]:5d} api_files={r[6]:4d}  {r[7]}')
