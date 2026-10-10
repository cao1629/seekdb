import ast, sys, pathlib
root = pathlib.Path(sys.argv[1])
src = (root/"tools/deploy/mysql_test/psmalltest.py").read_text()
tree = ast.parse(src)
mt = root/"tools/deploy/mysql_test"
avail = {f.stem for f in (mt/"t").glob("*.test")} | {f"{f.parent.parent.name}.{f.stem}" for f in (mt/"test_suite").glob("*/t/*.test")}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
        names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        present = [n for n in names if n in avail]
        print(node.targets[0].id, "entries:", len(names), "present in repo:", len(present), "absent:", len(names)-len(present))
