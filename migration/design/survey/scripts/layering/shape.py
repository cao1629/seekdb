"""Per edge: how many symbols are methods of a class declared in a lower header (Rust: inherent impl
must live with the type) vs free functions (Rust: needs an injected trait / fn table / extern binding),
and how many distinct classes / declaring headers / defining files / referencing files are involved.
Usage: python3 -I shape.py <classified.tsv>"""
import collections, sys
rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[1], encoding="utf-8")) if i]
E = collections.OrderedDict()
for r in rows:
    e = E.setdefault((r[0][4:], r[1][4:]), {"meth": 0, "free": 0, "classes": set(), "headers": set(), "defs": set(), "refs": set(), "declmod": collections.Counter()})
    if r[5] != "-":
        e["meth"] += 1
        e["classes"].add(r[5])
    else:
        e["free"] += 1
    e["headers"].update(h for h in r[6].split(";")[:1] if h != "?")
    e["defs"].update(x for x in r[8].split(";") if x and x != "?")
    e["refs"].update(x for x in r[10].split(";") if x)
    e["declmod"][r[7]] += 1
print("edge\tsymbols\tmethods\tfree\tclasses\tdecl_headers\tdef_files\tref_files(sample-capped)\tdecl_modules")
for (a, b), e in sorted(E.items(), key=lambda kv: -(kv[1]["meth"] + kv[1]["free"])):
    print("%s->%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s" % (a, b, e["meth"] + e["free"], e["meth"], e["free"], len(e["classes"]), len(e["headers"]), len(e["defs"]), len(e["refs"]), dict(e["declmod"])))
