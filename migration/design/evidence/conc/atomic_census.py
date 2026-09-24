#!/usr/bin/env python3
"""Census of ATOMIC_* macro calls in src/ at the frozen base.

Reads the working tree of /Users/colin/seekdb-dev/migrate-to-rust (src/ is identical to
834bbee1e; checked with `git diff --quiet 834bbee1e -- src`). Comments are stripped before
matching. For each call the first argument is extracted and reduced to the name it targets.
"""
import collections
import os
import re
import subprocess
import sys

ROOT = "/Users/colin/seekdb-dev/migrate-to-rust"
EXTS = (".h", ".hpp", ".cpp", ".cc", ".c", ".ipp", ".inc", ".def", ".cxx", ".hxx")


def list_files():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "src"], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\n") if f.endswith(EXTS)]


def strip_comments(text):
    res = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                break
            res.append("\n" * text.count("\n", i, j + 2))
            i = j + 2
            continue
        if c == '"' or c == "'":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "\n":
                    break
                j += 1
            res.append(text[i:j + 1])
            i = j + 1
            continue
        res.append(c)
        i += 1
    return "".join(res)


CALL = re.compile(r"\bATOMIC_([A-Z_0-9]+)\s*\(")


def first_arg(text, start):
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c in "([{<" and not (c == "<" and False):
            if c in "([{":
                depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[start:i]
            depth -= 1
        elif c == "," and depth == 0:
            return text[start:i]
        i += 1
    return text[start:]


CAST = re.compile(r"^\((?:const\s+)?(?:volatile\s+)?[A-Za-z_:][\w:<>\s,]*\*+\s*\)")
RCAST = re.compile(r"^(?:reinterpret_cast|static_cast|const_cast)\s*<[^>]*>\s*\((.*)\)$", re.S)


def reduce_target(arg):
    a = " ".join(arg.split())
    kinds = []
    changed = True
    while changed:
        changed = False
        a = a.strip()
        m = RCAST.match(a)
        if m:
            a = m.group(1)
            kinds.append("cast")
            changed = True
            continue
        m = CAST.match(a)
        if m:
            a = a[m.end():]
            kinds.append("cast")
            changed = True
            continue
        if a.startswith("&"):
            a = a[1:]
            kinds.append("addr")
            changed = True
            continue
        if a.startswith("(") and a.endswith(")"):
            depth = 0
            ok = True
            for k, ch in enumerate(a):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and k != len(a) - 1:
                        ok = False
                        break
            if ok:
                a = a[1:-1]
                changed = True
                continue
    element = False
    if a.endswith("]"):
        depth = 0
        for k in range(len(a) - 1, -1, -1):
            if a[k] == "]":
                depth += 1
            elif a[k] == "[":
                depth -= 1
                if depth == 0:
                    a = a[:k]
                    element = True
                    break
    if a.endswith(")"):
        return ("call-result", a, element, kinds)
    m = re.search(r"([A-Za-z_]\w*)\s*$", a)
    if not m:
        return ("other", a, element, kinds)
    name = m.group(1)
    through_member = bool(re.search(r"(\.|->)\s*" + re.escape(name) + r"\s*$", a))
    return ("name", name, element, kinds, through_member, "addr" in kinds)


def main():
    files = list_files()
    macro_count = collections.Counter()
    per_name = collections.defaultdict(list)
    kinds = collections.Counter()
    files_with = set()
    total = 0
    define_lines = 0
    for f in files:
        path = os.path.join(ROOT, f)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        text = strip_comments(text)
        for m in CALL.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_text = text[line_start:text.find("\n", m.start())]
            if line_text.lstrip().startswith("#define") or line_text.lstrip().startswith("#  define"):
                define_lines += 1
                continue
            macro = m.group(1)
            if f.endswith("ob_atomic.h") and "lib/atomic" in f:
                continue
            total += 1
            files_with.add(f)
            macro_count[macro] += 1
            arg = first_arg(text, m.end())
            r = reduce_target(arg)
            lineno = text.count("\n", 0, m.start()) + 1
            if r[0] == "name":
                _, name, element, ks, through_member, addr = r
                if element:
                    kinds["array element"] += 1
                elif through_member:
                    kinds["member via . or ->"] += 1
                elif addr and name.endswith("_"):
                    kinds["&name_ (own member)"] += 1
                elif addr:
                    kinds["&name (local, global or static)"] += 1
                else:
                    kinds["pointer value passed in"] += 1
                per_name[name].append((f, lineno, macro, element, through_member, addr))
            else:
                kinds[r[0]] += 1
    print("calls (outside #define lines and ob_atomic.h):", total)
    print("files:", len(files_with))
    print("#define lines skipped:", define_lines)
    print("by macro:")
    for k, v in macro_count.most_common():
        print(f"  {v:5d} ATOMIC_{k}")
    print("by target form:")
    for k, v in kinds.most_common():
        print(f"  {v:5d} {k}")
    names = set(per_name)
    trailing = {n for n in names if n.endswith("_")}
    print("distinct target names:", len(names))
    print("distinct target names ending in _ (member convention):", len(trailing))
    member_like = {n for n, uses in per_name.items() if n.endswith("_") or any(u[4] for u in uses)}
    print("distinct names ending in _ or reached through . / -> :", len(member_like))
    top = sorted(per_name.items(), key=lambda kv: -len(kv[1]))[:40]
    print("top names:")
    for n, uses in top:
        print(f"  {len(uses):4d} {n}  ({len(set(u[0] for u in uses))} files)")
    with open(os.path.join(os.path.dirname(__file__), "atomic_names.tsv"), "w") as out:
        for n, uses in sorted(per_name.items()):
            for (f, ln, macro, element, tm, addr) in uses:
                out.write(f"{n}\t{f}:{ln}\tATOMIC_{macro}\t{int(element)}\t{int(tm)}\t{int(addr)}\n")


if __name__ == "__main__":
    main()
