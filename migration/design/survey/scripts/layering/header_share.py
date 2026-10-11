"""Share of each module's lines that live in headers (.h/.ipp/.inl), and template density.
Header-resident code is compiled into every C++ includer, so it cannot sit behind a C ABI.
Usage: python3 -I header_share.py <repo>"""
import collections, os, re, sys
repo = os.path.abspath(sys.argv[1])
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
hl, cl, tmpl = collections.Counter(), collections.Counter(), collections.Counter()
inbound = collections.Counter()
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/nodes.tsv"))):
    if i == 0:
        continue
    p, n, c, u = l.rstrip("\n").split("\t")
    m = p.split("/")[1]
    if re.search(r"\.(h|hpp|ipp|inl)$", p):
        hl[m] += int(n)
        try:
            t = COMMENT.sub(" ", open(os.path.join(repo, p), encoding="utf-8", errors="replace").read())
            tmpl[m] += len(re.findall(r"\btemplate\s*<", t))
        except OSError:
            pass
    else:
        cl[m] += int(n)
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/edges.tsv"))):
    if i:
        a, b, k, c, ln = l.rstrip("\n").split("\t")
        ma, mb = a.split("/")[1], b.split("/")[1]
        if ma != mb:
            inbound[mb] += 1
print("module\theader_lines\timpl_lines\theader_share\ttemplate_decls\tinbound_cross_module_include_edges")
for m in sorted(hl, key=lambda x: -(hl[x] + cl[x])):
    print("%s\t%d\t%d\t%.0f%%\t%d\t%d" % (m, hl[m], cl[m], 100.0 * hl[m] / max(1, hl[m] + cl[m]), tmpl[m], inbound[m]))
