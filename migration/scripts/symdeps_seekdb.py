#!/usr/bin/env python3
"""Link-level module dependencies of seekdb, read from a build's object files (stdlib + nm).

Kit Step 1, prompts/01-dependency-map.md. The include graph (depmap_seekdb.py)
misses dependencies only the linker sees: functions declared `extern` locally
instead of through a header, and weak defaults in a lower module that an
upper module overrides with a strong definition (OB_WEAK_SYMBOL,
src/oblib/lib/utility/ob_macro_utils.h:20). The crate partition has to be
acyclic at this level too.

Every object file under BUILD/src is read with `nm -m -g` (the macOS format)
and turned into module-level edges:
  ref            module A has an undefined symbol that module B defines strongly
                 (B != A); count = distinct symbols
  weak_override  module W defines a symbol weakly and module S != W defines it
                 strongly, so the linker binds W's own calls to S
Symbols that are only ever defined weakly (inline functions, template
instantiations) are not edges: their code comes from headers, which the
include graph already covers. A module is src/<first directory> of the
object's path below BUILD/src. Skipped, and listed in skipped.tsv: objects
compiled only from untracked build-time output (the bison/flex parsers, which
are generated code), objects compiled only from vendored sources (an upstream
notice, the rule depmap_seekdb.py uses), and partial links outside CMakeFiles/
that repeat their member objects.

The edges describe one build configuration (this one: macOS, release);
platform-specific code of other targets is not in it.

Outputs, in OUT (default migration/depmap/symbols)
  module_edges.tsv    from, to, kind, count, example (demangled)
  weak_overrides.tsv  symbol, weak_in, strong_in, weak_default_source (OB_WEAK_SYMBOL file:line)
  duplicates.tsv      symbols defined strongly in more than one module
  skipped.tsv         objects left out, with the reason and their sources
  report.txt

Usage
  python3 -I migration/scripts/symdeps_seekdb.py --build build_release [--repo .] [--out migration/depmap/symbols]
"""
import argparse
import collections
import concurrent.futures
import os
import re
import subprocess
import sys

LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$")
WEAK_SITE = re.compile(r"OB_WEAK_SYMBOL\s+(?:[\w:<>*&\s]+?\s)?([A-Za-z_][\w:]*)\s*\(")
# Same vendored-code rule as depmap_seekdb.py: an upstream notice in the first 80 lines.
THIRD_PARTY_NOTICE = re.compile(r"(?i)\bcopyright\b|\bpublic domain\b")
OWN_NOTICE = re.compile(r"(?i)oceanbase|alibaba|ant group|antfin|apache license|www\.apache\.org")
UNITY_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def object_sources(root, rel, repo):
    """Repo paths of the sources an object under BUILD/src was compiled from."""
    if "/Unity/" in rel:
        with open(os.path.join(root, rel[:-2]), encoding="utf-8", errors="replace") as handle:
            return [os.path.relpath(p, repo).replace(os.sep, "/") for p in UNITY_INCLUDE.findall(handle.read())]
    owner, _, rest = rel.partition("/CMakeFiles/")
    source = rest.split("/", 1)[1][:-2].replace("__/", "../")
    return [os.path.normpath(os.path.join("src", owner, source)).replace(os.sep, "/")]


def vendored(repo, source):
    try:
        with open(os.path.join(repo, source), encoding="utf-8", errors="replace") as handle:
            head = handle.read().split("\n")[:80]
    except OSError:
        return False
    return any(THIRD_PARTY_NOTICE.search(l) and not OWN_NOTICE.search(l) for l in head)


def objects(build, repo, tracked):
    """(root, objects kept, skipped rows). Skipped: partial links outside CMakeFiles/ (they
    repeat their member objects), objects compiled only from untracked build-time output
    (bison/flex parsers: generated code), and objects compiled only from vendored sources."""
    root = os.path.join(build, "src")
    found, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.endswith(".o"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            if "/CMakeFiles/" not in rel:
                skipped.append((rel, "partial link outside CMakeFiles", "-"))
                continue
            sources = object_sources(root, rel, repo)
            if sources and all(s not in tracked for s in sources):
                skipped.append((rel, "compiled from build-time generated sources", ",".join(sources)))
            elif sources and all(vendored(repo, s) for s in sources):
                skipped.append((rel, "compiled from vendored sources", ",".join(sources)))
            else:
                found.append(rel)
    return root, found, skipped


def read_object(path):
    """(strong, weak, undefined) symbol sets of one object."""
    out = subprocess.run(["nm", "-m", "-g", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         check=False).stdout.decode("utf-8", "replace")
    strong, weak, undef = set(), set(), set()
    for line in out.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        name, sect, attrs = m.group("name"), m.group("sect"), m.group("attrs")
        if sect == "undefined":
            undef.add(name)
        elif "weak" in attrs.split():
            weak.add(name)
        else:
            strong.add(name)
    return strong, weak, undef


def demangle(names):
    names = sorted(set(names))
    if not names:
        return {}
    plain = [n[1:] if n.startswith("_") else n for n in names]
    res = subprocess.run(["c++filt"], input="\n".join(plain) + "\n", stdout=subprocess.PIPE,
                         universal_newlines=True, check=False).stdout.splitlines()
    if len(res) != len(names):
        return {n: p for n, p in zip(names, plain)}
    return dict(zip(names, res))


def weak_sites(repo):
    listing = subprocess.run(["git", "-C", repo, "grep", "-n", "OB_WEAK_SYMBOL", "--", "src"], stdout=subprocess.PIPE,
                             universal_newlines=True, check=False).stdout.splitlines()
    sites = []
    for line in listing:
        path, ln, text = line.split(":", 2)
        if "#define OB_WEAK_SYMBOL" in text:
            continue
        m = WEAK_SITE.search(text)
        if m:
            sites.append((m.group(1), "%s:%s" % (path, ln)))
    return sorted(sites)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="migration/depmap/symbols")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    tracked = set(subprocess.run(["git", "-C", repo, "ls-files", "-z"], stdout=subprocess.PIPE,
                                 check=True).stdout.decode("utf-8", "replace").split("\0"))
    root, objs, skipped = objects(args.build, repo, tracked)
    if not objs:
        sys.exit("no object files under %s/src" % args.build)
    module_of = {o: "src/" + o.split("/")[0] for o in objs}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda o: read_object(os.path.join(root, o)), objs))
    strong_in = collections.defaultdict(set)
    weak_in = collections.defaultdict(set)
    undef_in = collections.defaultdict(set)
    for obj, (strong, weak, undef) in zip(objs, results):
        mod = module_of[obj]
        for s in strong:
            strong_in[s].add(mod)
        for s in weak:
            weak_in[s].add(mod)
        for s in undef:
            undef_in[mod].add(s)

    refs = collections.defaultdict(set)
    for mod in sorted(undef_in):
        for s in undef_in[mod]:
            for owner in strong_in.get(s, ()):
                if owner != mod:
                    refs[(mod, owner)].add(s)
    overrides = collections.defaultdict(set)
    weak_rows = []
    for s in sorted(weak_in):
        strong = strong_in.get(s, set())
        losers = sorted(weak_in[s] - strong)
        if strong and losers:
            weak_rows.append((s, losers, sorted(strong)))
            for w in losers:
                for owner in strong:
                    overrides[(w, owner)].add(s)
    dups = sorted((s, sorted(m)) for s, m in strong_in.items() if len(m) > 1)

    examples = [min(v) for v in refs.values()] + [min(v) for v in overrides.values()] + [r[0] for r in weak_rows] + [d[0] for d in dups]
    names = demangle(examples)
    sites = weak_sites(args.repo)

    def site_for(demangled):
        hits = [where for fn, where in sites if re.search(r"(^|[\s:*&])" + re.escape(fn) + r"\(", demangled)]
        return ",".join(hits) or "-"

    os.makedirs(args.out, exist_ok=True)
    edges = sorted([(a, b, "ref", len(v), names[min(v)]) for (a, b), v in refs.items()] +
                   [(a, b, "weak_override", len(v), names[min(v)]) for (a, b), v in overrides.items()])
    with open(os.path.join(args.out, "module_edges.tsv"), "w") as f:
        f.write("from\tto\tkind\tcount\texample\n")
        for row in edges:
            f.write("%s\t%s\t%s\t%d\t%s\n" % row)
    with open(os.path.join(args.out, "weak_overrides.tsv"), "w") as f:
        f.write("symbol\tweak_in\tstrong_in\tweak_default_source\n")
        for s, losers, strong in weak_rows:
            f.write("%s\t%s\t%s\t%s\n" % (names[s], ",".join(losers), ",".join(strong), site_for(names[s])))
    with open(os.path.join(args.out, "duplicates.tsv"), "w") as f:
        f.write("symbol\tstrong_in\n")
        for s, mods in dups:
            f.write("%s\t%s\n" % (names[s], ",".join(mods)))
    unmatched_sites = [where for fn, where in sites if not any(re.search(r"(^|[\s:*&])" + re.escape(fn) + r"\(", names[r[0]]) for r in weak_rows)]
    with open(os.path.join(args.out, "skipped.tsv"), "w") as f:
        f.write("object\treason\tsources\n")
        for row in skipped:
            f.write("%s\t%s\t%s\n" % row)
    report = [
        "objects=%d modules=%s skipped=%s" % (len(objs), dict(sorted(collections.Counter(module_of.values()).items())),
                                             dict(sorted(collections.Counter(r[1] for r in skipped).items()))),
        "module edges: ref=%d weak_override=%d" % (sum(1 for e in edges if e[2] == "ref"), sum(1 for e in edges if e[2] == "weak_override")),
        "weak symbols overridden across modules=%d; OB_WEAK_SYMBOL sites in src=%d; sites with no cross-module override in this build=%d" % (
            len(weak_rows), len(sites), len(unmatched_sites)),
        "symbols strongly defined in more than one module=%d" % len(dups),
    ]
    with open(os.path.join(args.out, "report.txt"), "w") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
