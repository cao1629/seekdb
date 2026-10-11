"""Per edge: distinct referencing source files in the from-module and textual occurrences of the seam
keys (class name for methods, function name for free functions) in those files.
Usage: python3 -I callsites.py <repo> <classified.tsv>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl)$", f)]
text = {}
rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[2])) if i]
keys = collections.defaultdict(set)
for r in rows:
    k = r[5] if r[5] != "-" else r[4].lstrip("~")
    keys[(r[0], r[1])].add(k)
for (fr, to), ks in sorted(keys.items()):
    mod = fr + "/"
    fs = [f for f in files if f.startswith(mod)]
    if mod == "src/query/" or mod == "src/data_plane/":
        pass
    nfiles, nocc = 0, 0
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(ks, key=len, reverse=True)) + r")\b")
    for f in fs:
        if f not in text:
            text[f] = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
        n = len(pat.findall(text[f]))
        if n:
            nfiles += 1
            nocc += n
    print("%s -> %s\tkeys=%d\tref_files=%d\toccurrences=%d" % (fr[4:], to[4:], len(ks), nfiles, nocc))
