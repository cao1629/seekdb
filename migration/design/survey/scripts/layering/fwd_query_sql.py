"""Which query/api headers forward-declare sql-only types, and how many such types each uses.
Usage: python3 -I fwd_query_sql.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
hdrs = [f for f in files if re.search(r"\.(h|hpp|ipp|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
text = {f: COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read()) for f in hdrs}
DEF = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?([A-Za-z_]\w*)\b(?:\s+final)?\s*(?:\{|:(?!:))")
FWD = re.compile(r"^\s*(?:class|struct)\s+([A-Za-z_]\w*)\s*;", re.M)
defined_in = collections.defaultdict(set)
for f, t in text.items():
    for c in DEF.findall(t):
        defined_in[c].add(f.split("/")[1])
per = []
for f, t in text.items():
    if not f.startswith("src/query/"):
        continue
    cs = [c for c in set(FWD.findall(t)) if defined_in.get(c) == {"sql"} and len(re.findall(r"\b" + re.escape(c) + r"\b", t)) > 1]
    if cs:
        uses = sum(len(re.findall(r"\b" + re.escape(c) + r"\b", t)) - 1 for c in cs)
        per.append((len(cs), uses, f, sorted(cs)[:5]))
per.sort(reverse=True)
print("query headers using sql-only opaque types:", len(per))
for n, u, f, ex in per[:12]:
    print("  %3d types %4d uses  %s  %s" % (n, u, f, ex))
