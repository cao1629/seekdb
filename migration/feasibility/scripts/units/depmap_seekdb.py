#!/usr/bin/env python3
"""seekdb-adapted dependency mapper (stdlib only), derived from the migration
kit's scripts/depmap_c.py (same edges.tsv / order.txt / cycles.txt contract,
same iterative Tarjan SCC).

Differences from the kit starter:
  * `#include "..."` is resolved like the real build: including file's own
    directory first, then the include roots configured in CMake
    (src/CMakeLists.txt ob_base_without_pass + src/oblib/CMakeLists.txt
    oblib_base_without_pass + top-level include_directories), in that order.
    Files under src/oblib get only the oblib roots (oblib "exposes no
    src-reachable -I"); an oblib include that only resolves via the wider
    roots is still recorded as an edge but flagged as an `oblib_escape`.
  * `#include <...>` that resolves to an in-repo file via the -I roots is
    also an edge (flagged `angle`), since <lib/...> spellings exist.
  * comments and `#if 0` regions are stripped; each include is tagged as
    conditional when it sits under a non-guard #if/#ifdef.
  * .cpp/.cc/.c -> same-stem .h/.hpp edges, as in the kit.
  * extra outputs: unresolved.tsv, module/submodule/unit graphs + SCCs.

Usage: python3 -I depmap_seekdb.py REPO OUTDIR
"""
import collections
import os
import re
import sys

REPO = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)

NODE_EXTS = {".c", ".h", ".cc", ".cpp", ".hpp", ".ipp", ".def", ".map", ".y", ".l"}
IMPL_EXTS = {".c", ".cc", ".cpp"}
NODE_TOPS = ("src/", "deps/oblib/", "include/", "rust/sql-nio/include/",
             "tools/ob_error/", "tools/codestyle/", "bazel/")

# CMake order. <build>/generated, <build>/generated/share and
# <build>/generated/share/inner_table sit between "." and "src" but have no
# in-repo counterpart (configure-time outputs), so they are not listed here;
# misses that match their outputs are classified as generated below.
GLOBAL_ROOTS = ["rust/sql-nio/include", ".", "src", "src/query/api",
                "src/data_plane/api", "src/objit/include", "src/oblib/easy",
                "src/oblib", "src/oblib/common", "src/oblib/easy/include"]
OBLIB_ROOTS = ["rust/sql-nio/include", "src/oblib/easy", "src/oblib",
               "src/oblib/common", "src/oblib/easy/include"]

# --- file discovery ---------------------------------------------------------
all_repo_files = set()
nodes = []
for dp, dns, fns in os.walk(REPO):
    dns[:] = sorted(d for d in dns if d != ".git")
    for fn in sorted(fns):
        rel = os.path.relpath(os.path.join(dp, fn), REPO).replace(os.sep, "/")
        all_repo_files.add(rel)
        if os.path.splitext(fn)[1] in NODE_EXTS and rel.startswith(NODE_TOPS):
            nodes.append(rel)
node_set = set(nodes)

# --- comment stripping (keeps line structure, keeps string literals) ------
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


def parse(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as f:
        text = f.read()
    text = TOK.sub(_blank, text)
    lines = text.split("\n")
    stack = []  # frames: 'zero' | 'one' | 'cond' | 'guard'
    out = []
    pending_guard = None
    for ln, line in enumerate(lines, 1):
        m = DIRECTIVE.match(line)
        if not m:
            if line.strip():
                pending_guard = None
            continue
        d, rest = m.group(1), m.group(2).strip()
        if d == "include":
            dead = any(fr == "zero" for fr in stack)
            if dead:
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


def resolve(rel, inc, kind):
    """Return (target, how, ambiguous_targets)."""
    roots = OBLIB_ROOTS if rel.startswith("src/oblib/") else GLOBAL_ROOTS
    cands = []
    if kind == "q":
        cands.append(("dir", os.path.dirname(rel)))
    cands += [("root", r) for r in roots]
    hits = []
    for how, base in cands:
        p = norm(os.path.join(base, inc) if base not in ("", ".") else inc)
        if p and p in all_repo_files and p not in hits:
            hits.append(p)
    if hits:
        return hits[0], "ok", hits
    if rel.startswith("src/oblib/"):
        for r in GLOBAL_ROOTS:
            p = norm(os.path.join(r, inc) if r != "." else inc)
            if p and p in all_repo_files:
                return p, "oblib_escape", [p]
    return None, "miss", []


GENERATED_MISS = [
    (re.compile(r"(^|/)ob_inner_table_schema[^/]*$"), "inner-table generator (configure-time)"),
    (re.compile(r"(^|/)ob_all_virtual_sqlite_tables\.h$"), "inner-table generator (configure-time)"),
    (re.compile(r"_tab\.h$|_lex\.h$"), "bison/flex output (configure-time)"),
    (re.compile(r"(^|/)type_name\.c$"), "gen_type_name.sh output"),
]
SYSTEM_LIKE = re.compile(r"^(std[a-z]*\.h|string\.h|sys/|unistd\.h|errno\.h|math\.h|time\.h|limits\.h|signal\.h|pthread\.h|assert\.h|ctype\.h|fcntl\.h|malloc\.h|float\.h|inttypes\.h|setjmp\.h|locale\.h|wchar\.h|dlfcn\.h|execinfo\.h|sched\.h|netinet/|arpa/|netdb\.h|poll\.h|termios\.h|dirent\.h|windows\.h|io\.h|stdbool\.h|stdarg\.h|stddef\.h|config\.h|iostream|vector|string|map|algorithm)$|^(sys|linux|mach)/")

# --- edge extraction ----------------------------------------------------------
edges = {}           # (a, b) -> set(kinds)
unresolved = []      # (from, inc, kind, class)
stats = collections.Counter()
ambiguous = []
inc_records = []     # (from, to, kind, cond, how)
for rel in nodes:
    for inc, kind, cond, ln in parse(rel):
        stats["inc_" + kind] += 1
        tgt, how, hits = resolve(rel, inc, kind)
        if tgt is None:
            if kind == "q":
                cls = "other"
                for rx, label in GENERATED_MISS:
                    if rx.search(inc):
                        cls = label
                        break
                else:
                    if SYSTEM_LIKE.search(inc):
                        cls = "system header spelled with quotes"
                unresolved.append((rel, inc, cls))
                stats["q_unresolved"] += 1
            else:
                stats["a_external"] += 1
            continue
        if len(set(hits)) > 1:
            ambiguous.append((rel, inc, hits))
        if tgt not in node_set:
            stats["resolved_non_node_" + kind] += 1
            continue
        stats["%s_resolved" % kind] += 1
        if how == "oblib_escape":
            stats["oblib_escape"] += 1
        if cond:
            stats["%s_resolved_conditional" % kind] += 1
        inc_records.append((rel, tgt, kind, cond, how))
        if tgt != rel:
            edges.setdefault((rel, tgt), set()).add("angle" if kind == "a" else ("escape" if how == "oblib_escape" else "inc"))
    root, ext = os.path.splitext(rel)
    if ext in IMPL_EXTS:
        for hext in (".h", ".hpp"):
            if root + hext in node_set:
                edges.setdefault((rel, root + hext), set()).add("own_header")
                stats["own_header_edges"] += 1
                break

edge_list = sorted(edges)


# --- Tarjan (iterative), copied from the kit -----------------------------------
def tarjan_scc(nodes_, edges_):
    adj = {n: [] for n in nodes_}
    for a, b in edges_:
        adj[a].append(b)
    for n in adj:
        adj[n].sort()
    counter = [0]
    stack, on_stack = [], set()
    index, low = {}, {}
    sccs = []
    for start in nodes_:
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


def write(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text)


# --- file-level contract outputs -------------------------------------------------
sorted_nodes = sorted(nodes)
sccs = tarjan_scc(sorted_nodes, edge_list)
cycles = [s for s in sccs if len(s) > 1]
write("edges.tsv", "from\tto\tkind\n" + "".join(
    "%s\t%s\t%s\n" % (a, b, ",".join(sorted(edges[(a, b)]))) for a, b in edge_list))
write("order.txt", "# migration order: one batch per line, dependencies first\n" +
      "".join("\t".join(s) + "\n" for s in sccs))
write("cycles.txt", "# strongly connected components > 1 file: %d\n" % len(cycles) +
      "".join("\t".join(s) + "\n" for s in cycles))
write("unresolved.tsv", "from\tinclude\tclass\n" + "".join("%s\t%s\t%s\n" % u for u in unresolved))
write("ambiguous.tsv", "".join("%s\t%s\t%s\n" % (a, b, " | ".join(c)) for a, b, c in ambiguous))
write("include_records.tsv", "from\tto\tkind\tconditional\thow\n" + "".join(
    "%s\t%s\t%s\t%d\t%s\n" % (a, b, k, c, h) for a, b, k, c, h in inc_records))


# --- grouping helpers ---------------------------------------------------------------
def top_module(rel):
    p = rel.split("/")
    if p[0] == "src" and len(p) > 2:
        return "src/" + p[1]
    if p[0] in ("tools", "deps", "rust") and len(p) > 2:
        return p[0] + "/" + p[1]
    return p[0]


def sub_module(rel):
    p = rel.split("/")
    if p[0] == "src" and len(p) > 3:
        return "src/%s/%s" % (p[1], p[2])
    if p[0] == "src" and len(p) == 3:
        return "src/%s/(root)" % p[1]
    return top_module(rel)


def unit_of(rel):
    return os.path.splitext(rel)[0]


def collapse(keyfn):
    w = collections.Counter()
    for a, b in edge_list:
        ka, kb = keyfn(a), keyfn(b)
        if ka != kb:
            w[(ka, kb)] += 1
    keys = sorted({keyfn(n) for n in nodes})
    return keys, w


def greedy_fas(members, w):
    """Eades-Lin-Smyth greedy ordering; returns (order, back-edge weight)."""
    mem = set(members)
    out_w = collections.defaultdict(dict)
    in_w = collections.defaultdict(dict)
    for (a, b), c in w.items():
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
                    s2.insert(0, n); remaining.discard(n); changed = True
            for n in sorted(remaining):
                if not any(x in remaining for x in in_w[n]):
                    s1.append(n); remaining.discard(n); changed = True
        if remaining:
            best = max(sorted(remaining), key=lambda n: sum(c for x, c in out_w[n].items() if x in remaining) -
                       sum(c for x, c in in_w[n].items() if x in remaining))
            s1.append(best); remaining.discard(best)
    order = s1 + s2
    pos = {n: i for i, n in enumerate(order)}
    back = sum(c for (a, b), c in w.items() if a in mem and b in mem and pos[a] > pos[b])
    return order, back


report = []
report.append("files(nodes)=%d edges=%d (own_header=%d)" % (len(nodes), len(edge_list), stats["own_header_edges"]))
report.append("stats=" + repr(dict(stats)))
report.append("unresolved quoted includes=%d ambiguous(shadowed) includes=%d" % (len(unresolved), len(ambiguous)))
ucls = collections.Counter(u[2] for u in unresolved)
report.append("unresolved by class=" + repr(ucls.most_common()))
upre = collections.Counter(u[1].split("/")[0] + ("/" if "/" in u[1] else "") for u in unresolved)
report.append("unresolved top prefixes=" + repr(upre.most_common(30)))

# file-level SCC stats
sizes = sorted((len(s) for s in cycles), reverse=True)
buckets = collections.Counter()
for n in sizes:
    b = "2" if n == 2 else "3-5" if n <= 5 else "6-10" if n <= 10 else "11-50" if n <= 50 else "51-200" if n <= 200 else "201-1000" if n <= 1000 else ">1000"
    buckets[b] += 1
report.append("file SCCs total=%d cyclic(>1)=%d files_in_cycles=%d sizes_top10=%s buckets=%s" % (
    len(sccs), len(cycles), sum(sizes), sizes[:10], dict(buckets)))
lines_of = {}
for rel in nodes:
    with open(os.path.join(REPO, rel), "rb") as f:
        lines_of[rel] = f.read().count(b"\n")
if cycles:
    big = max(cycles, key=len)
    report.append("largest file SCC: %d files, %d lines; by module=%s" % (
        len(big), sum(lines_of[x] for x in big), collections.Counter(top_module(x) for x in big).most_common()))
    report.append("largest file SCC by submodule (top 25)=%s" % collections.Counter(sub_module(x) for x in big).most_common(25))
    write("largest_file_scc.txt", "\n".join(big) + "\n")
    report.append("2nd..6th file SCC sizes/modules=%s" % [
        (len(s), collections.Counter(top_module(x) for x in s).most_common(3)) for s in sorted(cycles, key=len, reverse=True)[1:6]])

# module graphs
for label, keyfn in (("top", top_module), ("sub", sub_module), ("unit", unit_of)):
    keys, w = collapse(keyfn)
    m_edges = sorted(w)
    m_sccs = tarjan_scc(keys, m_edges)
    m_cyc = [s for s in m_sccs if len(s) > 1]
    write("graph_%s_edges.tsv" % label, "from\tto\tfile_edges\n" + "".join(
        "%s\t%s\t%d\n" % (a, b, w[(a, b)]) for a, b in sorted(w, key=lambda e: -w[e])))
    write("graph_%s_sccs.txt" % label, "".join("%d\t%s\n" % (len(s), "\t".join(s)) for s in sorted(m_cyc, key=len, reverse=True)))
    csz = sorted((len(s) for s in m_cyc), reverse=True)
    report.append("[%s] nodes=%d edges=%d cyclic SCCs=%d sizes(top10)=%s nodes_in_cycles=%d" % (
        label, len(keys), len(m_edges), len(m_cyc), csz[:10], sum(csz)))
    if label in ("top", "sub"):
        for s in sorted(m_cyc, key=len, reverse=True)[:6]:
            mem = set(s)
            inner = sorted(((a, b, c) for (a, b), c in w.items() if a in mem and b in mem), key=lambda t: -t[2])
            order, back = greedy_fas(s, w)
            tot = sum(c for _, _, c in inner)
            report.append("  [%s] SCC size=%d members=%s" % (label, len(s), s if len(s) <= 40 else s[:40] + ["..."]))
            report.append("    internal cross edges=%d; greedy FAS back-edge weight=%d; greedy order(leaf-last)=%s" % (
                tot, back, order if len(order) <= 40 else order[:40] + ["..."]))
            if label == "top":
                report.append("    pair counts=%s" % inner)
    if label == "unit":
        if m_cyc:
            ubig = max(m_cyc, key=len)
            report.append("  largest unit SCC: %d units; by module=%s" % (
                len(ubig), collections.Counter(top_module(u + ".x") for u in ubig).most_common()))
            ub = collections.Counter()
            for n in csz:
                b = "2" if n == 2 else "3-5" if n <= 5 else "6-10" if n <= 10 else "11-50" if n <= 50 else "51-200" if n <= 200 else "201-1000" if n <= 1000 else ">1000"
                ub[b] += 1
            report.append("  unit SCC buckets=%s" % dict(ub))

# Bazel policy comparison (bazel/architecture/module_policy.bzl ALLOWED_MODULE_DEPS)
ALLOWED = {
    "data_plane": ["oblib", "share"],
    "logservice": ["data_plane", "oblib", "query", "share"],
    "objit": [], "oblib": [],
    "observer": ["data_plane", "logservice", "objit", "oblib", "pl", "query", "rootserver", "share", "sql", "storage", "standby"],
    "pl": ["data_plane", "oblib", "query", "share", "sql"],
    "query": ["objit", "oblib", "share"],
    "rootserver": ["data_plane", "logservice", "oblib", "pl", "query", "share", "sql", "storage"],
    "share": ["oblib"],
    "sql": ["data_plane", "oblib", "query", "share"],
    "storage": ["data_plane", "logservice", "oblib", "query", "share"],
    "standby": ["logservice", "oblib", "share", "storage"],
}
viol = collections.Counter()
viol_cond = collections.Counter()
viol_samples = collections.defaultdict(list)
for a, b in edge_list:
    ma, mb = top_module(a), top_module(b)
    if not (ma.startswith("src/") and mb.startswith("src/")) or ma == mb:
        continue
    ca, cb = ma[4:], mb[4:]
    if cb not in ALLOWED.get(ca, []):
        viol[(ca, cb)] += 1
        if len(viol_samples[(ca, cb)]) < 4:
            viol_samples[(ca, cb)].append("%s -> %s" % (a, b))
# conditional share of violating include records
for a, b, k, c, h in inc_records:
    ma, mb = top_module(a), top_module(b)
    if ma.startswith("src/") and mb.startswith("src/") and ma != mb and mb[4:] not in ALLOWED.get(ma[4:], []) and c:
        viol_cond[(ma[4:], mb[4:])] += 1
report.append("Bazel-policy violations (file edges, by consumer->producer)=%d over %d pairs" % (sum(viol.values()), len(viol)))
for (ca, cb), c in viol.most_common():
    report.append("  %s -> %s : %d (conditional includes among them: %d) e.g. %s" % (ca, cb, c, viol_cond[(ca, cb)], viol_samples[(ca, cb)][:2]))

# policy-consistent module graph check: drop violating edges, is it acyclic?
keys, w = collapse(top_module)
kept = [(a, b) for (a, b) in w if not (a.startswith("src/") and b.startswith("src/") and b[4:] not in ALLOWED.get(a[4:], []))]
k_sccs = [s for s in tarjan_scc(keys, sorted(kept)) if len(s) > 1]
report.append("top graph after removing policy-violating edges: cyclic SCCs=%s" % k_sccs)

write("report.txt", "\n".join(report) + "\n")
print("\n".join(report))
