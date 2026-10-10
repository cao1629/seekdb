import json, sys, runpy, pathlib
import os; sys.argv = ["classify.py", os.environ["REPO"], os.environ.get("OUT", "ownonly_out")]
pathlib.Path(sys.argv[2]).mkdir(exist_ok=True)
(pathlib.Path(sys.argv[2])/"ci_enabled.txt").write_text(open("ci_enabled.txt").read())
g = runpy.run_path("classify.py", run_name="not_main")
orig = g["gather"]
def own_only(test_file, bases):
    files, missing = orig(test_file, bases)
    return files[:1], missing
g["gather"] = own_only
g["classify"].__globals__["gather"] = own_only
g["main"]()
