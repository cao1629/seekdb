#!/usr/bin/env python3
"""Deterministic dependency map for the seekdb C/C++ tree (stdlib only).

Kit Step 1, prompts/01-dependency-map.md. Derived from the kit's
scripts/depmap_c.py (same edges.tsv / order.txt / cycles.txt contract, same
iterative Tarjan SCC) and from the feasibility survey's
migration/feasibility/scripts/units/depmap_seekdb.py.

What counts
  * In-repo means tracked by git (`git ls-files`). Build outputs written into
    the source tree (bison/flex parsers, inner-table schema headers) are not
    in-repo: an include of one is an unresolved include of class "configure".
  * Nodes are the tracked hand-written C/C++ files under src/ that the port
    translates: .h .hpp .ipp .inc .def .map .c .cc .cpp, minus the excluded
    classes. Excluded files are neither edges nor misses; an include of one is
    recorded in external_refs.tsv:
      third_party  vendored code: THIRD_PARTY_PREFIXES, or an upstream copyright /
                   public-domain notice in the first 80 lines (THIRD_PARTY_NOTICE)
      generated    checked-in generator output: a GENERATED rule (what each generator
                   writes), or a generated marker in the first 40 lines
      unbuilt      an implementation file the CMake build's source lists do not name
                   (CMake files, and the Bazel inventory variables CMake reads through
                   tools/cmake/emit_bazel_source_inventory.py) and no node includes
    Grammar files (.y/.l) are generator inputs, not nodes, and are not parsed.
  * An edge A -> B means A needs B:
      inc         A has `#include "..."` that resolves to B
      angle       A has `#include <...>` that resolves to B through an include root
      escape      A is under src/oblib and the include resolves only through
                  the wider src roots (oblib's own roots do not reach it)
      own_header  A is an implementation file and B its same-directory,
                  same-stem header (added even if A never includes B)
    Quoted includes try A's directory first, then the include roots in CMake
    order; angle includes try only the roots. Files under src/oblib get
    oblib's roots (src/oblib/CMakeLists.txt); everything else gets src's
    (src/CMakeLists.txt), which append oblib's. Comments and `#if 0` regions
    are stripped first; an include under any other #if/#ifdef counts
    (conservative) and is flagged conditional.

Outputs, in OUT (default migration/depmap)
  edges.tsv          from, to, kind, conditional, lines (1-based include lines)
  order.txt          file SCCs, dependencies first, one batch per line (kit contract)
  cycles.txt         file SCCs with more than one file (kit contract)
  nodes.tsv          path, lines, crate, unit
  excluded.tsv       path, class, reason
  external_refs.tsv  from, include, resolved, class, line
  unresolved.tsv     from, include, class, line (quoted includes no tracked file satisfies)
  units/             header groups (directory + stem): edges.tsv, order.txt, cycles.txt,
                     units.tsv, and representatives.txt (order.txt with one file per group,
                     the input for make_manifest.py)
  crates/            crate = src/<module>: include-level edges.tsv, cycles.txt; and, when
                     symbols/module_edges.tsv exists (symdeps_seekdb.py), the union
                     with link-level edges: union_edges.tsv, union_cycles.txt
  report.txt         counts

Usage
  python3 -I migration/scripts/depmap_seekdb.py [--repo .] [--out migration/depmap] [--build build_release]
      --build cross-checks the unbuilt class against what a configured CMake build
      compiled, and lists implementation nodes that build did not compile
  python3 -I migration/scripts/depmap_seekdb.py --show FILE [FILE ...]
      trial mode: print each file's parsed includes, how each resolved, and its edges
"""
import argparse
import ast
import collections
import fnmatch
import os
import re
import subprocess
import sys

NODE_EXTS = {".h", ".hpp", ".ipp", ".inc", ".def", ".map", ".c", ".cc", ".cpp"}
IMPL_EXTS = {".c", ".cc", ".cpp"}
HEADER_EXTS = (".h", ".hpp")

# src/CMakeLists.txt (ob_base_without_pass), then src/oblib/CMakeLists.txt
# (oblib_base_without_pass, linked by ob_base_without_pass), with the global
# include_directories(rust/sql-nio/include) from the top-level CMakeLists.txt
# first. The <build>/generated, <build>/generated/share and
# <build>/generated/share/inner_table roots sit between "." and "src" and hold
# configure-time output only, so they have no tracked counterpart.
SRC_ROOTS = ["rust/sql-nio/include", ".", "src", "src/query/api", "src/data_plane/api",
             "src/objit/include", "src/oblib/easy", "src/oblib", "src/oblib/common",
             "src/oblib/easy/include"]
OBLIB_ROOTS = ["rust/sql-nio/include", "src/oblib/easy", "src/oblib", "src/oblib/common",
               "src/oblib/easy/include"]

THIRD_PARTY_PREFIXES = {
    "src/oblib/lib/compress/zstd_1_3_8/": "zstd 1.3.8 sources",
}
# Vendored files keep their upstream notice, sometimes below the OceanBase license
# header (tzcode, libev, xxHash): a copyright or public-domain line in the first
# 80 lines that names nobody of OceanBase's own lineage marks the file third-party.
THIRD_PARTY_NOTICE = re.compile(r"(?i)\bcopyright\b|\bpublic domain\b")
OWN_NOTICE = re.compile(r"(?i)oceanbase|alibaba|ant group|antfin|apache license|www\.apache\.org")
# Checked-in generator output, one rule per thing a generator writes (fnmatch patterns,
# so a generator that emits more parts is still covered), plus any file whose first 40
# lines carry a generated marker (GENERATED_MARKER). Several generated files have no marker.
GENERATED = [
    ("src/share/ob_errno.h", "src/share/gen_errno.pl:110 <- src/share/ob_errno.def"),
    ("src/share/ob_errno.cpp", "src/share/gen_errno.pl:244 <- src/share/ob_errno.def"),
    ("src/oblib/lib/ob_errno.h", "src/share/gen_errno.pl:199 <- src/share/ob_errno.def"),
    ("src/share/system_variable/ob_system_variable_init.h", "src/share/system_variable/gen_ob_sys_variables.py:1173"),
    ("src/share/system_variable/ob_system_variable_init.cpp", "src/share/system_variable/gen_ob_sys_variables.py:1174"),
    ("src/share/system_variable/ob_system_variable_alias.h", "src/share/system_variable/gen_ob_sys_variables.py:1175"),
    ("src/share/system_variable/ob_sys_var_class_type.h", "src/share/system_variable/gen_ob_sys_variables.py:1176"),
    ("src/share/system_variable/ob_sys_var_meta.h", "src/share/system_variable/gen_ob_sys_variables.py:1178"),
    ("src/share/system_variable/ob_sys_var_meta.cpp", "src/share/system_variable/gen_ob_sys_variables.py:1179"),
    ("src/sql/session/ob_system_variable_factory.h", "src/share/system_variable/gen_ob_sys_variables.py:1180"),
    ("src/sql/session/ob_system_variable_factory.cpp", "src/share/system_variable/gen_ob_sys_variables.py:1181"),
    ("src/share/datum/ob_str_datum_funcs_compilation/ob_str_datum_funcs_compilation_*.cpp",
     "src/share/datum/ob_str_datum_funcs_compilation/gen_str_datum_func_parts.py:96"),
    ("src/share/datum/ob_str_datum_funcs_compilation/ob_str_datum_funcs_all.cpp",
     "src/share/datum/ob_str_datum_funcs_compilation/gen_str_datum_func_parts.py:151"),
    ("src/share/datum/ob_str_datum_funcs_compilation/ob_str_datum_funcs_compilation.ipp",
     "src/share/datum/ob_str_datum_funcs_compilation/gen_str_datum_func_parts.py:156"),
    ("src/sql/engine/expr/ob_expr_str_cmp_func_compile/ob_expr_str_cmp_func_part_*.cpp",
     "src/sql/engine/expr/ob_expr_str_cmp_func_compile/gen_expr_str_cmp_func.py:255"),
    ("src/sql/engine/expr/ob_expr_str_cmp_func_compile/ob_expr_str_cmp_func_all.cpp",
     "src/sql/engine/expr/ob_expr_str_cmp_func_compile/gen_expr_str_cmp_func.py:257"),
    ("src/oblib/lib/codec/ob_generated_scalar_bp_func.*", "src/oblib/lib/codec/generate_scalar_bitpakcing.py:339-342"),
    ("src/oblib/lib/codec/ob_generated_unalign_simd_bp_func.*", "src/oblib/lib/codec/generate_simd_bitpakcing.py"),
    ("src/oblib/grpc/*.pb.h", "protoc / grpc_cpp_plugin <- src/oblib/grpc/standbyservice.proto"),
    ("src/oblib/grpc/*.pb.cc", "protoc / grpc_cpp_plugin <- src/oblib/grpc/standbyservice.proto"),
    ("src/share/geo/*.pb-c.h", "protoc-c"),
    ("src/share/geo/*.pb-c.c", "protoc-c"),
    ("src/oblib/lib/charset/ob_uctype.h", "generated from UnicodeData-5.0.0d9.txt (line 21; the generator is not in the repo)"),
]
GENERATED_MARKER = re.compile(r"(do not edit|don't edit|auto[- ]?generated|automatically generated|"
                              r"this file is generated|generated file|@generated|generated by|generated from)", re.I)
# Marker hits checked by hand and found not to be generator output.
MARKER_FALSE_POSITIVES = {
    "src/sql/das/ob_das_dml_ctx_define.h",            # "cellids generated by one wkb"
    "src/sql/engine/expr/ob_expr_locate.cpp",         # "TODO Auto-generated destructor stub"
    "src/sql/engine/expr/ob_expr_position.cpp",
    "src/sql/engine/expr/ob_expr_conv.cpp",
    "src/sql/resolver/ddl/ob_analyze_stmt_resolver.cpp",
}
CONFIGURE_TIME = [
    (re.compile(r"(^|/)ob_inner_table_schema[^/]*\.(h|ipp)$"), "inner-table schema (generate_inner_table_schema.py at configure time)"),
    (re.compile(r"(^|/)ob_all_virtual_sqlite_tables\.h$"), "inner-table schema (generate_inner_table_schema.py at configure time)"),
    (re.compile(r"_tab\.h$|_lex\.h$"), "bison/flex output (build time)"),
    (re.compile(r"(^|/)type_name\.c$"), "gen_type_name.sh output (build time)"),
    (re.compile(r"\.pb\.h$"), "protoc output (build time)"),
    (re.compile(r"^config_(bridge|checkers)\.h$"), "rust/config build.rs output (build time)"),
]
SYSTEM_LIKE = re.compile(
    r"^(std[a-z]*\.h|string\.h|sys/|unistd\.h|errno\.h|math\.h|time\.h|limits\.h|signal\.h|pthread\.h|"
    r"assert\.h|ctype\.h|fcntl\.h|malloc\.h|float\.h|inttypes\.h|setjmp\.h|locale\.h|wchar\.h|dlfcn\.h|"
    r"execinfo\.h|sched\.h|netinet/|arpa/|netdb\.h|poll\.h|termios\.h|dirent\.h|windows\.h|io\.h|"
    r"stdbool\.h|stdarg\.h|stddef\.h|config\.h|iostream|vector|string|map|algorithm|cmath|libintl\.h|"
    r"arm_acle\.h|arm_neon\.h|immintrin\.h|x86intrin\.h)$|^(sys|linux|mach)/")
# Libraries installed from deps/ (third-party), sometimes included with quotes.
THIRD_PARTY_INCLUDE = re.compile(
    r"^(s2|vsag|roaring|boost|libxml2?|libxml|curl|openssl|google|grpcpp?|grpc|absl|rapidjson|lz4|zstd|snappy|"
    r"unicode|jemalloc|arrow|parquet|orc|re2|utf8proc|brotli|fast_float|antlr4|fmt|cpuinfo|mysql|mariadb|"
    r"gtest|gmock|protobuf-c|lua|llvm|clang|sqlite)/")
# CMake is the build CI and developers run (feasibility report, judgment 2). Its source
# lists are the CMake files themselves plus the variables of the data-only Bazel
# inventories that INVENTORY_EMITTER turns into CMake at configure time (its table of
# ("SEEKDB_...", "<file>.bzl", "<VARIABLE>") tuples); sources the inventories keep in
# other variables, such as *_IGNORED_SOURCES, are not built.
BUILD_FILE = re.compile(r"(^|/)CMakeLists\.txt$|\.cmake$")
INVENTORY_EMITTER = "tools/cmake/emit_bazel_source_inventory.py"
SOURCE_TOKEN = re.compile(r"[A-Za-z0-9_./+${}-]+\.(?:cpp|cc|c)\b")


# --- comment stripping (keeps line structure and string literals) ----------
TOK = re.compile(
    r'//[^\n]*'
    r'|/\*.*?\*/'
    r'|(?<![A-Za-z0-9_])(?:u8|u|U|L)?R"([^()\\\s]{0,16})\(.*?\)\1"'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'", re.S)


def _blank(m):
    s = m.group(0)
    if s.startswith("//"):
        return ""
    if s.startswith("/*"):
        return "\n" * s.count("\n")
    return s


DIRECTIVE = re.compile(r'^[ \t]*#[ \t]*(include|if|ifdef|ifndef|elif|else|endif|define)\b(.*)$')
INC_Q = re.compile(r'^\s*"([^"]+)"')
INC_A = re.compile(r'^\s*<([^>]+)>')


def parse_includes(text):
    """[(include, 'q'|'a', conditional, line)] outside comments and #if 0 regions."""
    text = TOK.sub(_blank, text)
    stack = []  # frames: 'zero' | 'one' | 'cond' | 'guard'
    out = []
    pending_guard = None
    for ln, line in enumerate(text.split("\n"), 1):
        m = DIRECTIVE.match(line)
        if not m:
            if line.strip():
                pending_guard = None
            continue
        d, rest = m.group(1), m.group(2).strip()
        if d == "include":
            if any(fr == "zero" for fr in stack):
                continue
            cond = any(fr == "cond" for fr in stack)
            mq = INC_Q.match(rest)
            if mq:
                out.append((mq.group(1), "q", cond, ln))
                continue
            ma = INC_A.match(rest)
            if ma:
                out.append((ma.group(1), "a", cond, ln))
            continue
        if d in ("if", "ifdef", "ifndef"):
            if d == "if" and re.match(r"^0\b", rest):
                stack.append("zero")
            elif d == "if" and re.match(r"^1\b", rest):
                stack.append("one")
            elif d == "ifndef" and not stack:
                stack.append("cond")
                pending_guard = rest.split()[0] if rest else None
                continue
            else:
                stack.append("cond")
        elif d == "define":
            name = rest.split("(")[0].split()[0] if rest else ""
            if pending_guard and name == pending_guard and stack and stack[-1] == "cond":
                stack[-1] = "guard"
        elif d in ("elif", "else"):
            if stack:
                if stack[-1] == "zero":
                    stack[-1] = "cond"
                elif stack[-1] == "one":
                    stack[-1] = "zero"
        elif d == "endif":
            if stack:
                stack.pop()
        pending_guard = None
    return out


def norm(p):
    p = os.path.normpath(p).replace(os.sep, "/")
    return None if p.startswith("../") or p == ".." else p


def tarjan_scc(nodes, edges):
    """Iterative Tarjan, copied from the kit; SCCs come out dependencies first."""
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


def greedy_order(members, weight):
    """Eades-Lin-Smyth greedy ordering of an SCC: (order with dependencies last, back edges)."""
    mem = set(members)
    out_w, in_w = collections.defaultdict(dict), collections.defaultdict(dict)
    for (a, b), c in weight.items():
        if a in mem and b in mem:
            out_w[a][b] = c
            in_w[b][a] = c
    remaining = set(mem)
    s1, s2 = [], []
    while remaining:
        changed = True
        while changed:
            changed = False
            for n in sorted(remaining):
                if not any(x in remaining for x in out_w[n]):
                    s2.insert(0, n)
                    remaining.discard(n)
                    changed = True
            for n in sorted(remaining):
                if not any(x in remaining for x in in_w[n]):
                    s1.append(n)
                    remaining.discard(n)
                    changed = True
        if remaining:
            best = max(sorted(remaining), key=lambda n: sum(c for x, c in out_w[n].items() if x in remaining)
                       - sum(c for x, c in in_w[n].items() if x in remaining))
            s1.append(best)
            remaining.discard(best)
    order = s1 + s2
    pos = {n: i for i, n in enumerate(order)}
    back = sorted((a, b) for (a, b) in weight if a in mem and b in mem and pos[a] > pos[b])
    return order, back


class Repo:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        listing = subprocess.run(["git", "-C", self.root, "ls-files", "-z"], stdout=subprocess.PIPE,
                                 check=True).stdout.decode("utf-8", "replace")
        self.tracked = sorted(p for p in listing.split("\0") if p)
        self.tracked_set = set(self.tracked)
        self._text = {}

    def text(self, rel):
        if rel not in self._text:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as handle:
                self._text[rel] = handle.read()
        return self._text[rel]

    def lines(self, rel):
        return self.text(rel).count("\n")


def classify(repo):
    """(candidates, excluded{path: (class, reason)}, marker_warnings)."""
    candidates, excluded, warnings = [], {}, []
    for rel in repo.tracked:
        if not rel.startswith("src/") or os.path.splitext(rel)[1] not in NODE_EXTS:
            continue
        third = next((why for pre, why in THIRD_PARTY_PREFIXES.items() if rel.startswith(pre)), None)
        if not third:
            for ln, line in enumerate(repo.text(rel).split("\n")[:80], 1):
                if THIRD_PARTY_NOTICE.search(line) and not OWN_NOTICE.search(line):
                    third = "upstream notice at line %d: %s" % (ln, line.strip().strip("/*").strip()[:100])
                    break
        generator = next((why for pattern, why in GENERATED if fnmatch.fnmatchcase(rel, pattern)), None)
        if not generator and rel not in MARKER_FALSE_POSITIVES:
            for ln, line in enumerate(repo.text(rel).split("\n")[:40], 1):
                if GENERATED_MARKER.search(line):
                    generator = "generated marker at line %d: %s" % (ln, line.strip().strip("/*").strip()[:100])
                    break
        if third:
            excluded[rel] = ("third_party", third)
        elif generator:
            excluded[rel] = ("generated", generator)
        else:
            candidates.append(rel)
    for pattern, _ in GENERATED:
        if not any(fnmatch.fnmatchcase(rel, pattern) for rel in repo.tracked):
            warnings.append("generated rule matches no tracked file: " + pattern)
    return candidates, excluded, warnings


def emitted_inventory(repo, warnings):
    """Source paths in the inventory variables INVENTORY_EMITTER hands to CMake."""
    if INVENTORY_EMITTER not in repo.tracked_set:
        warnings.append("inventory emitter %s not found" % INVENTORY_EMITTER)
        return set()
    refs = set()
    for node in ast.walk(ast.parse(repo.text(INVENTORY_EMITTER))):
        if isinstance(node, ast.Tuple) and len(node.elts) >= 3 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts[:3]):
            prefix, bzl, var = (e.value for e in node.elts[:3])
            if prefix.startswith("SEEKDB_") and bzl.endswith(".bzl"):
                refs.add((bzl, var))
    named = set()
    for bzl, var in sorted(refs):
        if bzl not in repo.tracked_set:
            warnings.append("inventory %s named by the emitter is not tracked" % bzl)
            continue
        assigns = {}
        for node in ast.parse(repo.text(bzl)).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigns[target.id] = node.value
        todo, seen = [var], set()
        if var not in assigns:
            warnings.append("inventory variable %s not found in %s" % (var, bzl))
        while todo:
            name = todo.pop()
            if name in seen or name not in assigns:
                continue
            seen.add(name)
            for sub in ast.walk(assigns[name]):
                if isinstance(sub, ast.Name) and sub.id in assigns:
                    todo.append(sub.id)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value.endswith((".cpp", ".cc", ".c")):
                    p = norm(sub.value if sub.value.startswith("src/") else os.path.join(os.path.dirname(bzl), sub.value))
                    if p:
                        named.add(p)
    return named


def named_by_build_lists(repo, warnings):
    """Repo paths of implementation files the CMake build's source lists name."""
    named = emitted_inventory(repo, warnings)
    for rel in repo.tracked:
        base = os.path.dirname(rel)
        if not BUILD_FILE.search(rel):
            continue
        for token in SOURCE_TOKEN.findall(repo.text(rel)):
            token = re.sub(r"\$\{[^}]*\}/?", "", token).lstrip("/")
            for cand in (os.path.join(base, token), token):
                p = norm(cand)
                if p:
                    named.add(p)
    return named


def compiled_in_build(build, repo_root):
    """Repo paths of the C/C++ files a configured CMake build compiled (unity bundles included)."""
    compiled = set()
    src_root = os.path.join(build, "src")
    inc = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames.sort()
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            if "/Unity/" in path.replace(os.sep, "/") and fn.endswith((".cxx", ".c")):
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for target in inc.findall(handle.read()):
                        rel = os.path.relpath(target, repo_root).replace(os.sep, "/")
                        if not rel.startswith("../"):
                            compiled.add(rel)
            elif fn.endswith(".o") and "/CMakeFiles/" in path.replace(os.sep, "/") and "/Unity/" not in path.replace(os.sep, "/"):
                rel_build = os.path.relpath(path, build).replace(os.sep, "/")
                owner, _, rest = rel_build.partition("/CMakeFiles/")
                source = rest.split("/", 1)[1][:-2].replace("__/", "../") if "/" in rest else ""
                rel = norm(os.path.join(owner, source))
                if rel:
                    compiled.add(rel)
    return compiled


class Mapper:
    def __init__(self, repo, build=None):
        self.repo = repo
        self.build = build
        self.candidates, self.excluded, self.marker_warnings = classify(repo)
        self.candidate_set = set(self.candidates)
        self.build_check = []

    def resolve(self, rel, inc, kind):
        """(target, how) with how in ok|escape|miss."""
        roots = OBLIB_ROOTS if rel.startswith("src/oblib/") else SRC_ROOTS
        cands = [os.path.dirname(rel)] if kind == "q" else []
        cands += roots
        for base in cands:
            p = norm(os.path.join(base, inc) if base not in ("", ".") else inc)
            if p and p in self.repo.tracked_set:
                return p, "ok"
        if rel.startswith("src/oblib/"):
            for base in SRC_ROOTS:
                p = norm(os.path.join(base, inc) if base != "." else inc)
                if p and p in self.repo.tracked_set:
                    return p, "escape"
        return None, "miss"

    def file_includes(self, rel):
        """[(include, kind, conditional, line, target, how)]"""
        out = []
        for inc, kind, cond, ln in parse_includes(self.repo.text(rel)):
            tgt, how = self.resolve(rel, inc, kind)
            out.append((inc, kind, cond, ln, tgt, how))
        return out

    def run(self):
        named = named_by_build_lists(self.repo, self.build_check)
        edges = collections.defaultdict(lambda: {"kinds": set(), "cond": True, "lines": []})
        external, unresolved = [], []
        stats = collections.Counter()
        for rel in self.candidates:
            for inc, kind, cond, ln, tgt, how in self.file_includes(rel):
                stats["include_" + kind] += 1
                if tgt is None:
                    if kind == "a":
                        stats["angle_external"] += 1
                        continue
                    cls = "other"
                    for rx, label in CONFIGURE_TIME:
                        if rx.search(inc):
                            cls = "configure: " + label
                            break
                    else:
                        if SYSTEM_LIKE.search(inc):
                            cls = "system header spelled with quotes"
                        elif THIRD_PARTY_INCLUDE.search(inc):
                            cls = "third-party header spelled with quotes"
                    unresolved.append((rel, inc, cls, ln))
                    continue
                if tgt in self.excluded:
                    external.append((rel, inc, tgt, self.excluded[tgt][0], ln))
                    continue
                if tgt not in self.candidate_set:
                    top = tgt.split("/")[0]
                    cls = "already_rust" if tgt.startswith("rust/") else ("grammar" if tgt.endswith((".y", ".l")) else "outside_src:" + top)
                    external.append((rel, inc, tgt, cls, ln))
                    continue
                if tgt == rel:
                    stats["self_include"] += 1
                    continue
                e = edges[(rel, tgt)]
                e["kinds"].add("angle" if kind == "a" else ("escape" if how == "escape" else "inc"))
                e["cond"] = e["cond"] and cond
                e["lines"].append(ln)
            stem, ext = os.path.splitext(rel)
            if ext in IMPL_EXTS:
                for hext in HEADER_EXTS:
                    if stem + hext in self.candidate_set:
                        e = edges[(rel, stem + hext)]
                        if not e["kinds"]:
                            e["cond"] = False
                        e["kinds"].add("own_header")
                        break
        # an unresolved spelling of a file the includer already reaches another way
        # (e.g. the other branch of an #if) adds no dependency
        targets_of = collections.defaultdict(set)
        for a, b in edges:
            targets_of[a].add(os.path.basename(b))
        unresolved = [(r, inc, "alternate spelling of an existing edge" if cls == "other" and os.path.basename(inc) in targets_of[r] else cls, ln)
                      for r, inc, cls, ln in unresolved]
        # unbuilt: implementation files no CMake file names and no candidate includes
        included = {b for (_, b) in edges}
        self.unbuilt = sorted(r for r in self.candidates if os.path.splitext(r)[1] in IMPL_EXTS
                              and r not in named and r not in included)
        if self.build:
            compiled = compiled_in_build(self.build, self.repo.root)
            wrong = sorted(set(self.unbuilt) & compiled)
            if wrong:
                self.build_check.append("ERROR compiled by %s but no CMake file names them (kept as nodes): %s" % (self.build, wrong))
                self.unbuilt = sorted(set(self.unbuilt) - compiled)
            impl = [r for r in self.candidates if os.path.splitext(r)[1] in IMPL_EXTS and r not in self.unbuilt]
            other_platform = sorted(r for r in impl if r not in compiled and r not in included)
            self.build_check.append("implementation nodes not compiled by %s (other platforms or options)=%d: %s" % (
                self.build, len(other_platform), other_platform))
        for r in self.unbuilt:
            self.excluded[r] = ("unbuilt", "not in the CMake build's source lists and not included by any node")
        drop = set(self.unbuilt)
        self.nodes = sorted(r for r in self.candidates if r not in drop)
        self.edges = {k: v for k, v in edges.items() if k[0] not in drop and k[1] not in drop}
        self.external = sorted(x for x in external if x[0] not in drop)
        self.unresolved = sorted(u for u in unresolved if u[0] not in drop)
        self.stats = stats
        return self


def crate_of(rel):
    parts = rel.split("/")
    return "src/" + parts[1] if parts[0] == "src" and len(parts) > 2 else parts[0]


def submodule_of(rel):
    parts = rel.split("/")
    if parts[0] == "src" and len(parts) > 3:
        return "src/%s/%s" % (parts[1], parts[2])
    return crate_of(rel) + "/(root)"


def unit_of(rel):
    return os.path.splitext(rel)[0]


def collapse(nodes, edge_keys, keyfn):
    weight = collections.Counter()
    evidence = collections.defaultdict(list)
    for a, b in edge_keys:
        ka, kb = keyfn(a), keyfn(b)
        if ka != kb:
            weight[(ka, kb)] += 1
            evidence[(ka, kb)].append((a, b))
    keys = sorted({keyfn(n) for n in nodes})
    return keys, weight, evidence


def write(out, name, text):
    path = os.path.join(out, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def batches_text(sccs, header):
    return header + "".join("\t".join(s) + "\n" for s in sccs)


def show(mapper, files):
    mapper.run()
    for rel in files:
        print("== %s (%s)" % (rel, "node" if rel in mapper.nodes else mapper.excluded.get(rel, ("not a node", ""))[0]))
        if rel not in mapper.candidate_set and rel not in mapper.excluded:
            continue
        for inc, kind, cond, ln, tgt, how in mapper.file_includes(rel):
            print("  L%-5d %s %-60s -> %s%s%s" % (ln, '"' if kind == "q" else "<", inc, tgt or "-", "" if how == "ok" else " [" + how + "]",
                                                 " (conditional)" if cond else ""))
        for (a, b), e in sorted(mapper.edges.items()):
            if a == rel:
                print("  EDGE -> %s [%s]%s" % (b, ",".join(sorted(e["kinds"])), " conditional" if e["cond"] else ""))


def read_symbol_edges(out):
    path = os.path.join(out, "symbols", "module_edges.tsv")
    if not os.path.exists(path):
        return None
    rows = []
    with open(path) as handle:
        next(handle)
        for line in handle:
            a, b, kind, count, example = line.rstrip("\n").split("\t")
            rows.append((a, b, kind, int(count), example))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="migration/depmap")
    ap.add_argument("--build", help="configured CMake build directory to cross-check the unbuilt class against")
    ap.add_argument("--show", nargs="+", metavar="FILE")
    args = ap.parse_args()
    repo = Repo(args.repo)
    mapper = Mapper(repo, os.path.abspath(args.build) if args.build else None)
    if args.show:
        show(mapper, args.show)
        return 0
    mapper.run()
    out = args.out
    nodes, edges = mapper.nodes, mapper.edges
    edge_keys = sorted(edges)
    report = []

    # file level (kit contract)
    sccs = tarjan_scc(nodes, edge_keys)
    cycles = [s for s in sccs if len(s) > 1]
    write(out, "edges.tsv", "from\tto\tkind\tconditional\tlines\n" + "".join(
        "%s\t%s\t%s\t%d\t%s\n" % (a, b, ",".join(sorted(edges[(a, b)]["kinds"])), edges[(a, b)]["cond"],
                                  ",".join(str(x) for x in sorted(set(edges[(a, b)]["lines"]))) or "-")
        for a, b in edge_keys))
    write(out, "order.txt", batches_text(sccs, "# migration order: one batch per line, dependencies first\n"))
    write(out, "cycles.txt", batches_text(cycles, "# strongly connected components > 1 file: %d\n" % len(cycles)))
    write(out, "nodes.tsv", "path\tlines\tcrate\tunit\n" + "".join(
        "%s\t%d\t%s\t%s\n" % (n, repo.lines(n), crate_of(n), unit_of(n)) for n in nodes))
    write(out, "excluded.tsv", "path\tclass\treason\n" + "".join(
        "%s\t%s\t%s\n" % (p, c, r) for p, (c, r) in sorted(mapper.excluded.items())))
    write(out, "external_refs.tsv", "from\tinclude\tresolved\tclass\tline\n" + "".join(
        "%s\t%s\t%s\t%s\t%d\n" % x for x in mapper.external))
    write(out, "unresolved.tsv", "from\tinclude\tclass\tline\n" + "".join("%s\t%s\t%s\t%d\n" % u for u in mapper.unresolved))
    kinds = collections.Counter(k for e in edges.values() for k in e["kinds"])
    report.append("nodes=%d lines=%d edges=%d kinds=%s conditional_edges=%d" % (
        len(nodes), sum(repo.lines(n) for n in nodes), len(edge_keys), dict(sorted(kinds.items())),
        sum(1 for e in edges.values() if e["cond"])))
    excl = collections.Counter(c for c, _ in mapper.excluded.values())
    report.append("excluded=%s" % dict(sorted(excl.items())))
    report.append("includes parsed=%s" % dict(sorted(mapper.stats.items())))
    report.append("external_refs=%d by class=%s" % (len(mapper.external), dict(sorted(collections.Counter(x[3] for x in mapper.external).items()))))
    report.append("unresolved quoted includes=%d by class=%s" % (len(mapper.unresolved), dict(sorted(collections.Counter(u[2] for u in mapper.unresolved).items()))))
    if mapper.marker_warnings:
        report.append("WARNING %s" % mapper.marker_warnings)
    report.extend(mapper.build_check)
    sizes = sorted((len(s) for s in cycles), reverse=True)
    report.append("[file] batches=%d cycles=%d files_in_cycles=%d sizes=%s" % (len(sccs), len(cycles), sum(sizes), sizes))

    # header groups (directory + stem)
    keys, weight, _ = collapse(nodes, edge_keys, unit_of)
    file_pos = {}
    for i, batch in enumerate(sccs):
        for f in batch:
            file_pos[f] = i
    unit_files = collections.defaultdict(list)
    for n in nodes:
        unit_files[unit_of(n)].append(n)
    u_sccs = tarjan_scc(keys, sorted(weight))
    # inside a unit cycle, order members by their earliest file's position in the file order
    u_sccs = [sorted(s, key=lambda u: (min(file_pos[f] for f in unit_files[u]), u)) for s in u_sccs]
    u_cycles = [s for s in u_sccs if len(s) > 1]
    write(out, "units/edges.tsv", "from\tto\tfile_edges\n" + "".join("%s\t%s\t%d\n" % (a, b, weight[(a, b)]) for a, b in sorted(weight)))
    write(out, "units/order.txt", batches_text(u_sccs, "# header groups (directory + stem), dependencies first; inside a cycle, members follow the file order\n"))
    rank = {".h": 0, ".hpp": 0, ".cpp": 1, ".cc": 1, ".c": 1}

    def representative(u):
        return min(unit_files[u], key=lambda f: (rank.get(os.path.splitext(f)[1], 2), f))
    write(out, "units/representatives.txt", batches_text(
        [[representative(u) for u in s] for s in u_sccs],
        "# units/order.txt with each header group written as one file (its header, else its implementation file);\n"
        "# input for make_manifest.py, one manifest row per header group\n"))
    write(out, "units/cycles.txt", "# unit SCCs > 1 unit: %d\n" % len(u_cycles) + "".join(
        "%d\t%d\t%s\n" % (len(s), sum(repo.lines(f) for u in s for f in unit_files[u]), "\t".join(s)) for s in sorted(u_cycles, key=len, reverse=True)))
    write(out, "units/units.tsv", "unit\tfiles\tlines\tcrate\n" + "".join(
        "%s\t%s\t%d\t%s\n" % (u, ",".join(os.path.basename(f) for f in sorted(unit_files[u])), sum(repo.lines(f) for f in unit_files[u]),
                              crate_of(unit_files[u][0])) for u in keys))
    usz = sorted((len(s) for s in u_cycles), reverse=True)
    report.append("[unit] units=%d edges=%d batches=%d cycles=%d units_in_cycles=%d sizes(top10)=%s" % (
        len(keys), len(weight), len(u_sccs), len(u_cycles), sum(usz), usz[:10]))

    # crates (src/<module>), include level
    ckeys, cweight, cevidence = collapse(nodes, edge_keys, crate_of)
    c_cycles = [s for s in tarjan_scc(ckeys, sorted(cweight)) if len(s) > 1]
    write(out, "crates/edges.tsv", "from\tto\tfile_edges\texample\n" + "".join(
        "%s\t%s\t%d\t%s -> %s\n" % (a, b, cweight[(a, b)], cevidence[(a, b)][0][0], cevidence[(a, b)][0][1]) for a, b in sorted(cweight)))
    lines = ["# crate = src/<module>; include-level SCCs > 1 crate: %d" % len(c_cycles)]
    for s in c_cycles:
        order, back = greedy_order(s, cweight)
        lines.append("SCC\t" + "\t".join(s))
        lines.append("  order (dependencies last)\t" + "\t".join(order))
        for a, b in back:
            for fa, fb in cevidence[(a, b)]:
                e = edges[(fa, fb)]
                lines.append("  back edge %s -> %s\t%s:%s -> %s [%s]" % (a, b, fa, ",".join(str(x) for x in sorted(set(e["lines"]))) or "-", fb,
                                                                         ",".join(sorted(e["kinds"]))))
    write(out, "crates/cycles.txt", "\n".join(lines) + "\n")
    report.append("[crate/include] crates=%d edges=%d cycles=%s" % (len(ckeys), len(cweight), c_cycles))

    # submodules, for the design doc's crate split
    skeys, sweight, _ = collapse(nodes, edge_keys, submodule_of)
    s_cycles = sorted((s for s in tarjan_scc(skeys, sorted(sweight)) if len(s) > 1), key=len, reverse=True)
    write(out, "crates/submodule_cycles.txt", "".join("%d\t%s\n" % (len(s), "\t".join(s)) for s in s_cycles))
    report.append("[submodule] submodules=%d edges=%d cycle sizes=%s" % (len(skeys), len(sweight), [len(s) for s in s_cycles]))

    # crates, include level + link level. Link edges are judged against the include
    # layering: "with" follows it (B is already an include-level dependency of A),
    # "against" closes a cycle (A is an include-level dependency of B), "across" joins
    # crates the include graph leaves unordered.
    sym = read_symbol_edges(out)
    if sym is not None:
        reach = {k: set() for k in ckeys}
        for k in ckeys:
            todo = [b for (a, b) in cweight if a == k]
            while todo:
                n = todo.pop()
                if n not in reach[k]:
                    reach[k].add(n)
                    todo.extend(b for (a, b) in cweight if a == n)
        include_order = [k for s in tarjan_scc(ckeys, sorted(cweight)) for k in s]
        union = collections.Counter(cweight)
        sym_ev = collections.defaultdict(list)
        link_rows = []
        for a, b, kind, count, example in sym:
            if a == b:
                continue
            union[(a, b)] += count
            sym_ev[(a, b)].append((kind, count, example))
            rel = "with" if b in reach.get(a, ()) else ("against" if a in reach.get(b, ()) else "across")
            link_rows.append((rel, a, b, kind, count, example))
        u_keys = sorted(set(ckeys) | {a for a, _ in union} | {b for _, b in union})
        uc = [s for s in tarjan_scc(u_keys, sorted(union)) if len(s) > 1]
        write(out, "crates/union_edges.tsv", "from\tto\tinclude_file_edges\tlink_refs\tlink_example\n" + "".join(
            "%s\t%s\t%d\t%s\t%s\n" % (a, b, cweight.get((a, b), 0), ",".join("%s:%d" % (k, c) for k, c, _ in sym_ev[(a, b)]) or "0",
                                      (sym_ev[(a, b)][0][2] if sym_ev[(a, b)] else "-")) for a, b in sorted(union)))
        lines = ["# crate = src/<module>",
                 "# include-level order, dependencies first: " + " ".join(include_order),
                 "# link-level edges judged against it: with = follows it; against = closes a cycle; across = orders crates it leaves unordered",
                 "relation\tfrom\tto\tkind\tsymbols\texample"]
        order_rank = {"against": 0, "across": 1, "with": 2}
        for row in sorted(link_rows, key=lambda r: (order_rank[r[0]], r[1], r[2], r[3])):
            lines.append("%s\t%s\t%s\t%s\t%d\t%s" % row)
        lines.append("# include + link-level SCCs > 1 crate: %d" % len(uc))
        for s in uc:
            lines.append("SCC\t" + "\t".join(s))
        write(out, "crates/union_cycles.txt", "\n".join(lines) + "\n")
        rel_count = collections.Counter(r[0] for r in link_rows)
        report.append("[crate/include+link] edges=%d link edges with/across/against the include order=%d/%d/%d; cycles=%s" % (
            len(union), rel_count["with"], rel_count["across"], rel_count["against"], uc))
    else:
        report.append("[crate/include+link] skipped: %s missing (run symdeps_seekdb.py)" % os.path.join(out, "symbols", "module_edges.tsv"))

    write(out, "report.txt", "\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
