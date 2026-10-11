"""Count api headers that have a same-basename implementation file (.cpp/.ipp/.c) in another module,
and compare with the nm attribution. Usage: python3 -I api_basename.py <repo> <api_impl.tsv>"""
import collections, os, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
impl = collections.defaultdict(list)
for f in files:
    b, ext = os.path.splitext(os.path.basename(f))
    if ext in (".cpp", ".ipp", ".c", ".cc"):
        impl[b].append(f)
api = [f for f in files if (f.startswith("src/query/api/") or f.startswith("src/data_plane/api/")) and f.endswith(".h")]
same = {}
for h in api:
    b = os.path.splitext(os.path.basename(h))[0]
    others = [f for f in impl.get(b, []) if f.split("/")[1] != h.split("/")[1]]
    if others:
        same[h] = others
mods = collections.Counter(f.split("/")[1] for v in same.values() for f in v[:1])
print("api headers with a same-basename impl file in another module:", len(same), dict(mods))
nm = {}
for i, l in enumerate(open(sys.argv[2])):
    if i:
        h, m = l.rstrip("\n").split("\t")
        nm[h] = m
elsewhere = {h for h, m in nm.items() if m != "-" and any(x.split(":")[0] != h.split("/")[1] for x in m.split(","))}
print("nm-attributed elsewhere:", len(elsewhere), " both:", len(elsewhere & set(same)), " basename-only:", len(set(same) - elsewhere), " nm-only:", len(elsewhere - set(same)))
for h in sorted(set(same) - elsewhere)[:10]:
    print("  basename-only:", h, same[h][:2])
