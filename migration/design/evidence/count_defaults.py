import re
import subprocess
import os
import sys

sys.path.insert(0, "/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence")
src = open("/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/count_constructs.py").read()
ns = {}
exec(src.split("PATTERNS = {")[0], ns)
strip = ns["strip_comments_and_strings"]
ROOT = ns["ROOT"]

files = subprocess.run(["git", "-C", ROOT, "ls-files", "src"], capture_output=True, text=True).stdout.split()
files = [f for f in files if f.endswith((".h", ".hpp", ".ipp")) and "zstd_src" not in f]

sig = re.compile(r"[\s\*&:~](\w+)\s*\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*|final\s*)*(?:=\s*0\s*)?[;{:]")
ctrl = {"if", "for", "while", "switch", "return", "sizeof", "catch", "defined", "decltype", "static_assert", "alignof", "__attribute__"}
decls = 0
with_default = 0
default_params = 0
for f in files:
    text = strip(open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read())
    text = re.sub(r"^\s*#.*$", "", text, flags=re.M)
    for m in sig.finditer(text):
        name, params = m.group(1), m.group(2)
        if name in ctrl or name.isupper():
            continue
        if not params.strip() or params.strip() == "void":
            decls += 1
            continue
        parts = []
        depth = 0
        cur = ""
        for ch in params:
            if ch in "<([":
                depth += 1
            elif ch in ">)]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        if not all(re.search(r"[\w\*&>]\s*[\*&]*\s*\w+\s*(=.*)?$", p.strip(), re.S) for p in parts):
            continue
        if not all(re.match(r"\s*(?:const\s+)?[\w:]", p) for p in parts):
            continue
        decls += 1
        n = sum(1 for p in parts if re.search(r"[^=!<>]=[^=]", p))
        if n:
            with_default += 1
            default_params += n
print("header function-like declarations", decls)
print("declarations with >=1 default argument", with_default)
print("default parameters", default_params)
