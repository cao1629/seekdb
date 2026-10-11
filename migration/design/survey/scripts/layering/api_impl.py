"""Which modules implement the out-of-line symbols declared in src/query/api and src/data_plane/api headers.

For every strong symbol in every object (nm), derive (scope, name). A symbol is attributed to an api
header if (a) its class is defined in that header, or (b) it is a free function whose name is declared
in that header (`name(` present, header mentions the innermost namespace) and no non-api header defines
a class with that scope. Header -> set of defining modules.

Usage: python3 -I api_impl.py <repo> <outdir>
"""
import collections, concurrent.futures, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
out = sys.argv[2]
root = os.path.join(repo, "build_release", "src")
LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
api = [f for f in files if (f.startswith("src/query/api/") or f.startswith("src/data_plane/api/")) and f.endswith(".h")]
text = {f: COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read()) for f in api}
CLS = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?([A-Za-z_]\w*)\b(?:\s+final)?\s*(?:\{|:(?!:))")
cls_hdr = collections.defaultdict(set)
for f, t in text.items():
    for c in CLS.findall(t):
        cls_hdr[c].add(f)
FN = re.compile(r"(?<![\w.>:~])([a-z_][a-z0-9_]*)\s*\(")
fn_hdr = collections.defaultdict(set)
for f, t in text.items():
    # only namespace-scope-ish declarations: lines that end a declaration with ');' and are not indented methods
    for m in re.finditer(r"^(?:[A-Za-z_][\w:<>\*&\s,]*?)\s([a-z_][a-z0-9_]*)\s*\([^;{]*\)\s*;", t, re.M):
        fn_hdr[m.group(1)].add(f)

objs = []
for d, dn, fn in os.walk(root):
    for x in fn:
        p = os.path.join(d, x)
        if x.endswith(".o") and "/CMakeFiles/" in p:
            objs.append(os.path.relpath(p, root))


def rd(rel):
    o = subprocess.run(["nm", "-m", "-g", os.path.join(root, rel)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
    s = set()
    for line in o.splitlines():
        m = LINE.match(line)
        if m and m.group("sect") != "undefined" and "weak" not in m.group("attrs").split():
            s.add(m.group("name"))
    return s


with concurrent.futures.ThreadPoolExecutor(6) as p:
    res = list(p.map(rd, objs))
sym_mod = collections.defaultdict(set)
for o, s in zip(objs, res):
    for x in s:
        sym_mod[x].add(o.split("/")[0])
names = sorted(sym_mod)
plain = [n[1:] if n.startswith("_") else n for n in names]
dem = subprocess.run(["c++filt"], input="\n".join(plain) + "\n", stdout=subprocess.PIPE, universal_newlines=True).stdout.splitlines()


def split(d):
    if " for " in d and (d.startswith("vtable") or d.startswith("typeinfo") or d.startswith("VTT")):
        q = d.split(" for ", 1)[1]
        q = re.sub(r"<[^<>]*(<[^<>]*>[^<>]*)*>", "", q)
        return q.split("::"), True
    depth = 0
    cut = len(d)
    for i, ch in enumerate(d):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth == 0 and d[i - 8:i] != "operator":
            cut = i
            break
    q = re.sub(r"<[^<>]*(<[^<>]*>[^<>]*)*>", "", d[:cut])
    return q.split("::"), False


hdr_mods = collections.defaultdict(collections.Counter)
for n, d in zip(names, dem):
    parts, is_vt = split(d)
    if len(parts) < 1:
        continue
    hdrs = set()
    if is_vt:
        hdrs = cls_hdr.get(parts[-1], set())
    else:
        cls = parts[-2] if len(parts) >= 2 else ""
        if cls and cls in cls_hdr:
            hdrs = cls_hdr[cls]
            outer = parts[-3] if len(parts) >= 3 else ""
            if outer and outer[:1].isupper():
                hdrs = {h for h in hdrs if outer in cls_hdr and h in cls_hdr[outer]}
            elif outer:
                hdrs = {h for h in hdrs if ("namespace " + outer) in text[h]}
        elif parts[-1] in fn_hdr and (len(parts) < 2 or not parts[-2][:1].isupper()):
            ns = parts[-2] if len(parts) >= 2 else ""
            hdrs = {h for h in fn_hdr[parts[-1]] if not ns or ("namespace " + ns) in text[h]}
    for h in hdrs:
        for m in sym_mod[n]:
            hdr_mods[h][m] += 1

own = lambda h: h.split("/")[1]
impl_elsewhere = {h for h, c in hdr_mods.items() if any(m != own(h) for m in c)}
impl_own_only = {h for h, c in hdr_mods.items() if c and all(m == own(h) for m in c)}
no_symbols = set(api) - set(hdr_mods)
print("api headers:", len(api), " query/api:", sum(1 for h in api if h.startswith("src/query")), " data_plane/api:", sum(1 for h in api if h.startswith("src/data_plane")))
print("headers with out-of-line symbols defined in another module:", len(impl_elsewhere))
print("  of which query/api:", sum(1 for h in impl_elsewhere if h.startswith("src/query")), " data_plane/api:", sum(1 for h in impl_elsewhere if h.startswith("src/data_plane")))
print("headers whose symbols are defined only in their own module:", len(impl_own_only))
print("headers with no out-of-line symbols found (header-only: inline/templates/pure interfaces/types):", len(no_symbols))
mods = collections.Counter()
for h in impl_elsewhere:
    for m in hdr_mods[h]:
        if m != own(h):
            mods[m] += 1
print("implementing modules (count of api headers):", dict(mods.most_common()))
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "api_impl.tsv"), "w") as f:
    f.write("header\tdefining_modules(symbol counts)\n")
    for h in sorted(api):
        f.write("%s\t%s\n" % (h, ",".join("%s:%d" % kv for kv in sorted(hdr_mods.get(h, {}).items())) or "-"))
