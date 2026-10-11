"""Dump every symbol behind selected link-level module edges, with referencing and defining objects.

Same nm logic as migration/scripts/symdeps_seekdb.py (macOS `nm -m -g`), but keeps full symbol
lists instead of one example per edge.

Usage: python3 -I edge_symbols.py <repo> <outdir>
Writes <outdir>/edge_symbols.tsv: from, to, kind, mangled, demangled, ref_objects, def_objects
"""
import collections, concurrent.futures, os, re, subprocess, sys

repo = os.path.abspath(sys.argv[1])
out = sys.argv[2]
build = os.path.join(repo, "build_release")
root = os.path.join(build, "src")
LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$")
UNITY_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
THIRD_PARTY_NOTICE = re.compile(r"(?i)\bcopyright\b|\bpublic domain\b")
OWN_NOTICE = re.compile(r"(?i)oceanbase|alibaba|ant group|antfin|apache license|www\.apache\.org")

tracked = set(subprocess.run(["git", "-C", repo, "ls-files", "-z"], stdout=subprocess.PIPE,
                             check=True).stdout.decode("utf-8", "replace").split("\0"))


def object_sources(rel):
    if "/Unity/" in rel:
        with open(os.path.join(root, rel[:-2]), encoding="utf-8", errors="replace") as h:
            return [os.path.relpath(p, repo) for p in UNITY_INCLUDE.findall(h.read())]
    owner, _, rest = rel.partition("/CMakeFiles/")
    source = rest.split("/", 1)[1][:-2].replace("__/", "../")
    return [os.path.normpath(os.path.join("src", owner, source))]


def vendored(source):
    try:
        with open(os.path.join(repo, source), encoding="utf-8", errors="replace") as h:
            head = h.read().split("\n")[:80]
    except OSError:
        return False
    return any(THIRD_PARTY_NOTICE.search(l) and not OWN_NOTICE.search(l) for l in head)


objs = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames.sort()
    for fn in sorted(filenames):
        if not fn.endswith(".o"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, fn), root)
        if "/CMakeFiles/" not in rel:
            continue
        srcs = object_sources(rel)
        if srcs and all(s not in tracked for s in srcs):
            continue
        if srcs and all(vendored(s) for s in srcs):
            continue
        objs.append(rel)


def read_object(rel):
    o = subprocess.run(["nm", "-m", "-g", os.path.join(root, rel)], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, check=False).stdout.decode("utf-8", "replace")
    strong, weak, undef = set(), set(), set()
    for line in o.splitlines():
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


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(read_object, objs))

mod_of = {o: "src/" + o.split("/")[0] for o in objs}
strong_objs = collections.defaultdict(set)   # sym -> objs defining strongly
weak_objs = collections.defaultdict(set)
undef_objs = collections.defaultdict(set)    # sym -> objs referencing
for o, (s, w, u) in zip(objs, results):
    for x in s:
        strong_objs[x].add(o)
    for x in w:
        weak_objs[x].add(o)
    for x in u:
        undef_objs[x].add(o)

rows = []
for sym, refs in undef_objs.items():
    owners = strong_objs.get(sym)
    if not owners:
        continue
    owner_mods = {mod_of[o] for o in owners}
    by_mod = collections.defaultdict(list)
    for r in refs:
        by_mod[mod_of[r]].append(r)
    for fm, robjs in by_mod.items():
        for tm in owner_mods:
            if tm == fm:
                continue
            rows.append((fm, tm, "ref", sym, sorted(robjs), sorted(o for o in owners if mod_of[o] == tm)))
for sym, wos in weak_objs.items():
    owners = strong_objs.get(sym)
    if not owners:
        continue
    wmods = {mod_of[o] for o in wos} - {mod_of[o] for o in owners}
    for wm in wmods:
        for tm in {mod_of[o] for o in owners}:
            rows.append((wm, tm, "weak_override", sym, sorted(o for o in wos if mod_of[o] == wm),
                         sorted(o for o in owners if mod_of[o] == tm)))

names = sorted({r[3] for r in rows})
plain = [n[1:] if n.startswith("_") else n for n in names]
dem = subprocess.run(["c++filt"], input="\n".join(plain) + "\n", stdout=subprocess.PIPE,
                     universal_newlines=True, check=False).stdout.splitlines()
dmap = dict(zip(names, dem)) if len(dem) == len(names) else dict(zip(names, plain))

os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "edge_symbols.tsv"), "w") as f:
    f.write("from\tto\tkind\tmangled\tdemangled\tref_objects\tdef_objects\n")
    for r in sorted(rows):
        f.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (r[0], r[1], r[2], r[3], dmap[r[3]], ",".join(r[4]), ",".join(r[5])))
cnt = collections.Counter((r[0], r[1], r[2]) for r in rows)
for k, v in sorted(cnt.items()):
    print("%s\t%s\t%s\t%d" % (k[0], k[1], k[2], v))
