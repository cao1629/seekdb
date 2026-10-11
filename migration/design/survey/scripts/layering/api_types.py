"""How C++-specific are the api-layer interfaces (what a C ABI boundary would have to wrap)?
Counts, over src/query/api and src/data_plane/api headers, files containing / occurrences of C++-only
interface vocabulary. Usage: python3 -I api_types.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src/query/api", "src/data_plane/api"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
files = [f for f in files if f.endswith(".h")]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
P = {
    "template<": r"\btemplate\s*<",
    "virtual": r"\bvirtual\b",
    "ObIAllocator": r"\bObIAllocator\b",
    "ObIArray<": r"\bObIArray\s*<",
    "ObString": r"\bObString\b",
    "ObDatum/ObObj": r"\bOb(?:Datum|Obj)\b",
    "reference param (&)": r"\w\s*&\s*\w+\s*[,)]",
    "std::": r"\bstd::",
}
occ = collections.Counter()
fh = collections.Counter()
for f in files:
    t = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
    for k, p in P.items():
        n = len(re.findall(p, t))
        if n:
            occ[k] += n
            fh[k] += 1
print("api headers:", len(files))
for k in P:
    print("  %-22s files=%3d occurrences=%5d" % (k, fh[k], occ[k]))
