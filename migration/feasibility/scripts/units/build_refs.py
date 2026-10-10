#!/usr/bin/env python3
"""Which on-disk implementation files are named by any Bazel BUILD/.bzl file?
Usage: python3 -I build_refs.py REPO OUTDIR"""
import os, re, sys, collections
repo, out = sys.argv[1], sys.argv[2]
STR = re.compile(r'"([^"\n]+\.(?:cpp|cc|c|h|ipp|def))"')
refs = set()
for dp, dns, fns in os.walk(repo):
    dns[:] = [d for d in dns if d != ".git"]
    for fn in fns:
        if fn == "BUILD.bazel" or fn.endswith(".bzl") or fn == "CMakeLists.txt" or fn.endswith(".cmake"):
            p = os.path.join(dp, fn)
            pkg = os.path.relpath(dp, repo).replace(os.sep, "/")
            txt = open(p, encoding="utf-8", errors="replace").read()
            for s in STR.findall(txt):
                s = s.lstrip("/").replace(":", "/")
                refs.add(s)
                refs.add(os.path.normpath(os.path.join(pkg, s)).replace(os.sep, "/"))
impl, unref = [], []
for dp, dns, fns in os.walk(os.path.join(repo, "src")):
    for fn in fns:
        if os.path.splitext(fn)[1] in (".cpp", ".cc", ".c"):
            rel = os.path.relpath(os.path.join(dp, fn), repo).replace(os.sep, "/")
            impl.append(rel)
            if rel.startswith("src/standby/"):
                continue  # srcs = glob(["**/*.cpp"]) in src/standby/BUILD.bazel
            parts = rel.split("/")
            hit = any("/".join(parts[i:]) in refs for i in range(len(parts)))
            if not hit:
                unref.append(rel)
def lines(r):
    with open(os.path.join(repo, r), "rb") as f:
        return f.read().count(b"\n")
by = collections.Counter(); byl = collections.Counter()
for r in unref:
    m = "/".join(r.split("/")[:2]); by[m] += 1; byl[m] += lines(r)
with open(os.path.join(out, "unreferenced_impl.tsv"), "w") as f:
    for r in sorted(unref, key=lambda r: -lines(r)):
        f.write("%s\t%d\n" % (r, lines(r)))
print("impl files under src=%d, not named in any BUILD/.bzl/CMake file=%d, lines=%d" % (len(impl), len(unref), sum(byl.values())))
print("by module:", [(m, by[m], byl[m]) for m in sorted(by, key=lambda m: -byl[m])])
