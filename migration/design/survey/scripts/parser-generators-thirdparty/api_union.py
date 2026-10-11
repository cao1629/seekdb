#!/usr/bin/env python3
"""Precise API call-site counts for selected libraries over all tracked src C/C++ files
(excluding vendored + generated files listed in migration/depmap/excluded.tsv).
Usage: python3 -I api_sites.py REPO FILELIST
Prints: library, files with >=1 API line, API lines, top files.
"""
import os, re, sys, collections
repo, flist = sys.argv[1], sys.argv[2]
EXT = {'.c', '.h', '.cc', '.cpp', '.hpp', '.ipp', '.y', '.l'}
excluded = set()
for line in open(os.path.join(repo, 'migration/depmap/excluded.tsv')):
    p = line.split('\t')[0]
    if p != 'path':
        excluded.add(p)
files = [l.strip() for l in open(flist) if l.strip()]
src = [f for f in files if f.startswith('src/') and os.path.splitext(f)[1] in EXT and f not in excluded]
APIS = [
 ('OpenSSL', r'\b(EVP_[A-Za-z]\w*|SSL_[a-z]\w*|SSL_CTX\w*|RSA_\w+|BIO_\w+|HMAC\w*|RAND_bytes|RAND_\w+|ERR_(get|peek|error|print|clear)\w*|AES_\w+|PEM_\w+|X509_\w+|PKCS5_\w+|MD5_(Init|Update|Final)|MD5\s*\(|MD4\s*\(|SHA(1|224|256|384|512)?_(Init|Update|Final)|SHA(1|224|256|384|512)\s*\(|DES_\w+|OPENSSL_\w+|CRYPTO_\w+|ENGINE_\w+)\b'),
 ('Boost.Geometry', r'\bboost::geometry::|\bbg::|\bbgm::|\bbgi::|\bbgd::|\bbgsr::|\bbg_srs::'),
 ('ICU', r'\bu(regex|col|cnv|brk|loc|text|norm2?|str|set|trans|idna)_\w+\s*\(|\bU_FAILURE\b|\bU_SUCCESS\b|\bURegularExpression\b|\bUText\b|\bUErrorCode\b|\bU_REGEX_\w+|\bUREGEX_\w+|\bicu::'),
 ('libxml2', r'\bxml[A-Z]\w*\b|\bhtml[A-Z]\w*\s*\(|\bXML_(PARSE|ERR|SAX|NODE)\w*\b'),
 ('libcurl', r'\bcurl_\w+\s*\(|\bCURL(OPT|E|INFO|M)?_\w+\b|\bCURL\s*\*'),
 ('zlib', r'\b(deflate(Init2?|End|Reset|Bound)?|inflate(Init2?|End|Reset)?|compress2|uncompress|compressBound)\s*\(|\bz_stream\b|\bZ_(OK|STREAM_END|BUF_ERROR|DEFAULT_COMPRESSION|FINISH|NO_FLUSH|SYNC_FLUSH)\b'),
 ('s2geometry', r'\bS2(Cell|CellId|CellUnion|LatLng|LatLngRect|Polygon|Polyline|Loop|Point|Cap|RegionCoverer|Region|Earth|Shape\w*|Builder\w*|BooleanOperation|LatLngRectBounder|Testing|Cell\w*)\b|\bS1(Angle|ChordAngle|Interval)\b|\bR[12](Interval|Rect)\b'),
 ('CRoaring', r'\broaring(64)?_bitmap_\w+|\broaring::\w+|\broaring64_\w+|\broaring_(bitmap|iterator|uint32_iterator|statistics|free|malloc|init)\w*'),
 ('vsag', r'\bvsag::'),
 ('SQLite', r'\bsqlite3_\w+|\bsqlite3\b\s*\*'),
 ('RapidJSON', r'\brapidjson::'),
 ('protobuf-c', r'\bprotobuf_c_\w+|\bvector_tile__\w+|\bVectorTile__\w+'),
 ('gRPC/protobuf C++', r'\bgrpc::|\bgoogle::protobuf::'),
 ('libaio', r'\bio_(submit|getevents|setup|destroy|cancel|prep_pread|prep_pwrite|prep_\w+)\s*\(|\bstruct\s+iocb\b|\bio_context_t\b|\bstruct\s+io_event\b'),
 ('Boost.Spirit', r'\bqi::|\bphoenix::|\bboost::spirit'),
 ('fast_float', r'\bfast_float::'),
 ('libunwind', r'\bunw_\w+'),
 ('jemalloc', r'\bje_\w+\s*\(|\bmallctl\w*\s*\(|\bmallocx\s*\(|\bsdallocx\s*\('),
 ('ZSTD API', r'\bZSTD_\w+\s*\('),
 ('xxHash API', r'\bXXH(32|64|3_\w+|128)\w*\s*\('),
]
cache = {}; ALL = set()
for name, rx in APIS:
    r = re.compile(rx)
    per = collections.Counter()
    for f in src:
        if f not in cache:
            try:
                cache[f] = open(os.path.join(repo, f), encoding='utf-8', errors='replace').read().splitlines()
            except FileNotFoundError:
                cache[f] = []
        for line in cache[f]:
            s = line.strip()
            if s.startswith(('//', '*', '/*', '#include')):
                continue
            if r.search(line):
                per[f] += 1
    tot = sum(per.values())
    ALL.update(per.keys())
print('union of files with direct third-party API lines:', len(ALL))
import collections as _c; print(_c.Counter(f.split('/')[1] for f in ALL).most_common())
