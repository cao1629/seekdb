"""Count C++ uses of the Rust config crate's generated getters (config::<name>(...)) per module.
Usage: python3 -I count_config.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE,
                       check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
PAT = re.compile(r"(?<![\w:])(?:oceanbase::)?config::([a-z_][a-z0-9_]*)\s*\(")
by_mod = collections.Counter()
names = collections.Counter()
fileset = set()
for f in files:
    t = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
    hits = PAT.findall(t)
    if hits:
        by_mod[f.split("/")[1]] += len(hits)
        fileset.add(f)
        names.update(hits)
print("total", sum(by_mod.values()), "files", len(fileset), "distinct getters", len(names))
print(dict(sorted(by_mod.items())))
print(names.most_common(10))
