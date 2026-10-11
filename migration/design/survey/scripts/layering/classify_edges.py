"""For each symbol behind the against/across link edges, find:
  - the declaring header(s) (class definition for methods; declaration for free functions) and their module
  - the defining source file inside the defining module (by scanning the def objects' sources)
  - the referencing source files inside the referencing module (files that use the name)
Heuristic text search; results are per-symbol rows plus per-edge summaries.

Usage: python3 -I classify_edges.py <repo> <edge_symbols.tsv> <outdir>
"""
import collections, os, re, subprocess, sys

repo = os.path.abspath(sys.argv[1])
tsv = sys.argv[2]
out = sys.argv[3]
build_src = os.path.join(repo, "build_release", "src")
UNITY_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

AGAINST = {("src/oblib", "src/observer"), ("src/oblib", "src/share"), ("src/oblib", "src/storage"),
           ("src/pl", "src/observer"), ("src/rootserver", "src/observer"), ("src/share", "src/observer"),
           ("src/sql", "src/observer"), ("src/sql", "src/pl"), ("src/sql", "src/rootserver"),
           ("src/storage", "src/observer"), ("src/storage", "src/rootserver")}
ACROSS = {("src/pl", "src/storage"), ("src/sql", "src/storage"), ("src/storage", "src/sql")}

files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE,
                       check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl)$", f)]
text = {}
for f in files:
    try:
        text[f] = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
    except OSError:
        pass
headers = [f for f in text if re.search(r"\.(h|hpp|ipp|inl)$", f)]


def module(path):
    p = path.split("/")
    m = p[1]
    if m in ("data_plane", "query") and len(p) > 2 and p[2] == "api":
        return m + "/api"
    return m


def obj_sources(rel):
    if "/Unity/" in rel:
        with open(os.path.join(build_src, rel[:-2]), encoding="utf-8", errors="replace") as h:
            return [os.path.relpath(p, repo) for p in UNITY_INCLUDE.findall(h.read())]
    owner, _, rest = rel.partition("/CMakeFiles/")
    source = rest.split("/", 1)[1][:-2].replace("__/", "../")
    return [os.path.normpath(os.path.join("src", owner, source))]


def split_name(dem):
    """('ns::Class', 'method', is_method_guess) from a demangled signature."""
    if dem.startswith("vtable for ") or dem.startswith("typeinfo") or dem.startswith("VTT for "):
        q = dem.split(" for ", 1)[1]
        parts = q.split("::")
        return "::".join(parts[:-1]), parts[-1], "vtable"
    base = dem
    # cut the parameter list: find the first '(' at template depth 0
    depth = 0
    cut = len(base)
    for i, ch in enumerate(base):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth == 0:
            if base[i - 8:i] == "operator":
                continue
            cut = i
            break
    q = base[:cut]
    q = re.sub(r"<[^<>]*(<[^<>]*>[^<>]*)*>", "", q)  # drop template args
    parts = q.split("::")
    return "::".join(parts[:-1]), parts[-1], "func"


cls_def_cache = {}


def class_headers(cls):
    if cls in cls_def_cache:
        return cls_def_cache[cls]
    pat = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?" + re.escape(cls) + r"\b(?:\s+final)?\s*(?:\{|:(?!:))")
    hits = [h for h in headers if cls in text[h] and pat.search(text[h])]
    cls_def_cache[cls] = hits
    return hits


def func_headers(ns, fn):
    pat = re.compile(r"(?<![\w.>:])" + re.escape(fn) + r"\s*\(")
    last_ns = ns.split("::")[-1] if ns else ""
    hits = []
    for h in headers:
        t = text[h]
        if fn not in t:
            continue
        if not pat.search(t):
            continue
        if last_ns and ("namespace " + last_ns) not in t:
            continue
        hits.append(h)
    return hits


rows = []
for line in open(tsv, encoding="utf-8"):
    if line.startswith("from\t"):
        continue
    fr, to, kind, mangled, dem, refo, defo = line.rstrip("\n").split("\t")
    if (fr, to) not in AGAINST and (fr, to) not in ACROSS:
        continue
    scope, name, how = split_name(dem)
    cls = None
    decl = []
    if how == "vtable":
        cls = name
        decl = class_headers(cls)
    else:
        # method if the last scope component names a class defined in some header
        last = scope.split("::")[-1] if scope else ""
        if last and last[0].isupper():
            cls = last
            decl = class_headers(cls)
        if not decl:
            if name.startswith("~"):
                name = name[1:]
            decl = func_headers(scope, name)
            cls = None if not decl else cls
    # definition source in the defining module
    def_srcs = set()
    for o in defo.split(","):
        if o:
            def_srcs.update(obj_sources(o))
    defpat = re.compile((re.escape(cls) + r"\s*::\s*" if cls else r"(?<![\w:.>])") + re.escape(name.lstrip("~")) + r"\s*\(")
    def_files = sorted(s for s in def_srcs if s in text and defpat.search(text[s]))
    ref_srcs = set()
    for o in refo.split(","):
        if o:
            ref_srcs.update(obj_sources(o))
    key = cls or name
    refpat = re.compile(r"\b" + re.escape(key.lstrip("~")) + r"\b")
    ref_files = sorted(s for s in ref_srcs if s in text and refpat.search(text[s]))
    rows.append((fr, to, kind, scope, name, cls or "-", ";".join(decl) or "?",
                 ";".join(sorted({module(d) for d in decl})) or "?", ";".join(def_files) or "?",
                 len(ref_files), ";".join(ref_files[:6]), dem))

os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "classified.tsv"), "w") as f:
    f.write("from\tto\tkind\tscope\tname\tclass\tdecl_headers\tdecl_modules\tdef_files\tn_ref_files\tref_files_sample\tdemangled\n")
    for r in rows:
        f.write("\t".join(str(x) for x in r) + "\n")

summary = collections.defaultdict(collections.Counter)
for r in rows:
    summary[(r[0], r[1])][r[7]] += 1
for k in sorted(summary):
    print("%s -> %s : %s" % (k[0], k[1], dict(summary[k].most_common())))
