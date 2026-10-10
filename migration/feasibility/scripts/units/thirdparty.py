#!/usr/bin/env python3
"""Count src/ files that #include each third-party library (angle or quoted).
Usage: python3 -I thirdparty.py REPO"""
import os, re, sys, collections
repo = sys.argv[1]
LIBS = [
 ("vsag", r"^vsag/"), ("ICU", r"^unicode/"), ("libxml2", r"^libxml2?/"), ("curl", r"^curl/"),
 ("openssl", r"^openssl/"), ("zlib", r"^zlib\.h$|^zconf\.h$"), ("zstd (system)", r"^zstd\.h$|^zstd/"), ("lz4", r"lz4"),
 ("snappy", r"snappy"), ("xz/lzma", r"^lzma\.h$|^lzma/"), ("protobuf (C++)", r"^google/protobuf/"),
 ("protobuf-c", r"^protobuf-c/"), ("gRPC", r"^grpcpp?/|^grpc/"), ("s2geometry", r"^s2/"), ("CRoaring", r"^roaring/|^roaring\.h"),
 ("boost", r"^boost/"), ("jemalloc", r"^jemalloc/|jemalloc\.h$"), ("libunwind", r"^libunwind"), ("lua", r"^lua|^lauxlib|^lualib"),
 ("LLVM", r"^llvm/|^llvm-c/|^clang/"), ("cos/oss/aws SDK", r"^cos_|^oss_|^aws/|^alibabacloud"),
 ("arrow/parquet/orc", r"^arrow/|^parquet/|^orc/"), ("re2", r"^re2/"), ("utf8proc", r"utf8proc"), ("brotli", r"^brotli/"),
 ("sqlite", r"sqlite3\.h$"), ("rapidjson", r"^rapidjson/"), ("fast_float", r"^fast_float/"), ("abseil", r"^absl/"),
 ("antlr4", r"antlr4"), ("libaio", r"^libaio\.h$"), ("fmt", r"^fmt/"), ("cpuinfo", r"^cpuinfo"), ("openblas/cblas", r"cblas|lapack|openblas"),
 ("diskann", r"diskann"), ("mysql/mariadb client", r"^mysql\.h$|^mariadb/|^mysql/|errmsg\.h$"), ("mxml", r"mxml"),
 ("gtest/gmock", r"^gtest/|^gmock/"), ("libeasy (in-repo)", r"^easy_|^io/easy_|^util/easy_|^thread/easy_"),
 ("sql-nio (Rust C ABI)", r"^nio\.h$"), ("ussl", r"ussl"),
]
INC = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
files_per = collections.defaultdict(set)
mods_per = collections.defaultdict(collections.Counter)
for dp, dns, fns in os.walk(os.path.join(repo, "src")):
    for fn in fns:
        if os.path.splitext(fn)[1] not in {".c", ".h", ".cc", ".cpp", ".hpp", ".ipp", ".y", ".l"}:
            continue
        p = os.path.join(dp, fn); rel = os.path.relpath(p, repo)
        txt = open(p, encoding="utf-8", errors="replace").read()
        for inc in INC.findall(txt):
            for name, rx in LIBS:
                if re.search(rx, inc):
                    files_per[name].add(rel); mods_per[name][rel.split("/")[1]] += 1
for name, _ in LIBS:
    fs = files_per.get(name, set())
    print("%-24s files=%4d  modules=%s  e.g. %s" % (name, len(fs), dict(mods_per[name].most_common(4)), sorted(fs)[:2]))
