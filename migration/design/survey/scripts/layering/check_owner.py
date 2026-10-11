"""Check that every object's owner directory (build_release/src/<mod>/CMakeFiles/...) matches
the src/<mod> of every source compiled into it. Usage: python3 -I check_owner.py <repo>"""
import os, re, sys, collections
repo = os.path.abspath(sys.argv[1])
build = os.path.join(repo, "build_release")
root = os.path.join(build, "src")
UNITY_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
mism = collections.Counter()
examples = {}
total = 0
for dirpath, dirnames, filenames in os.walk(root):
    for fn in filenames:
        if not fn.endswith(".o"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, fn), root)
        if "/CMakeFiles/" not in rel:
            continue
        owner_mod = rel.split("/")[0]
        if "/Unity/" in rel:
            with open(os.path.join(root, rel[:-2]), encoding="utf-8", errors="replace") as h:
                srcs = [os.path.relpath(p, repo) for p in UNITY_INCLUDE.findall(h.read())]
        else:
            owner, _, rest = rel.partition("/CMakeFiles/")
            source = rest.split("/", 1)[1][:-2].replace("__/", "../")
            srcs = [os.path.normpath(os.path.join("src", owner, source))]
        for s in srcs:
            total += 1
            parts = s.split("/")
            if parts[0] != "src":
                k = (owner_mod, "non-src:" + parts[0])
            elif parts[1] != owner_mod:
                k = (owner_mod, parts[1])
            else:
                continue
            mism[k] += 1
            examples.setdefault(k, s)
for k, v in sorted(mism.items()):
    print(k, v, examples[k])
print("sources checked", total)
