#!/usr/bin/env python3
"""Pick the two skeptic reviewers' files for a depmap review round (prompts/01), deterministically.

Each reviewer gets 20 files:
  * 10 random, from non-overlapping halves of the sorted node list (A: first half,
    B: second half), seeded by the round number;
  * 10 adversarial. A: the 5 largest nodes and the 5 with the most preprocessor
    conditionals. B: the 6 nodes with the most platform conditionals, and 4
    excluded files (generated, third-party, unbuilt) to confirm the map leaves them out.
Files used in earlier rounds are skipped, so every round samples fresh files.

Usage: python3 -I migration/scripts/depmap_review_sample.py --round N [--depmap migration/depmap]
Writes DEPMAP/review/roundN/sample-A.txt and sample-B.txt (path<TAB>category).
"""
import argparse
import os
import random
import re

COND = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif)\b", re.M)
PLATFORM = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b[^\n]*(_WIN32|_MSC_VER|__APPLE__|__linux__|__ANDROID__|"
                      r"__aarch64__|__x86_64__|__ARM_NEON|__AVX|__SSE|__powerpc|__loongarch)", re.M)


def read_tsv(path):
    with open(path) as handle:
        header = next(handle).rstrip("\n").split("\t")
        return [dict(zip(header, line.rstrip("\n").split("\t"))) for line in handle]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--depmap", default="migration/depmap")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    nodes = read_tsv(os.path.join(args.depmap, "nodes.tsv"))
    excluded = read_tsv(os.path.join(args.depmap, "excluded.tsv"))
    used = set()
    for r in range(1, args.round):
        for who in ("A", "B"):
            path = os.path.join(args.depmap, "review", "round%d" % r, "sample-%s.txt" % who)
            if os.path.exists(path):
                with open(path) as handle:
                    used |= {line.split("\t")[0] for line in handle if line.strip()}

    def text(rel):
        with open(os.path.join(args.repo, rel), encoding="utf-8", errors="replace") as handle:
            return handle.read()

    paths = sorted(n["path"] for n in nodes)
    lines = {n["path"]: int(n["lines"]) for n in nodes}
    fresh = [p for p in paths if p not in used]
    picked = set()

    def take(candidates, k, category, out):
        for p in candidates:
            if len([x for x in out if x[1] == category]) >= k:
                break
            if p not in picked and p not in used:
                picked.add(p)
                out.append((p, category))

    a, b = [], []
    take(sorted(fresh, key=lambda p: (-lines[p], p)), 5, "adversarial: largest", a)
    cond = {p: len(COND.findall(text(p))) for p in fresh}
    take(sorted(fresh, key=lambda p: (-cond[p], p)), 5, "adversarial: most preprocessor conditionals", a)
    plat = {p: len(PLATFORM.findall(text(p))) for p in fresh}
    take(sorted(fresh, key=lambda p: (-plat[p], p)), 6, "adversarial: platform-specific", b)
    by_class = {}
    for row in excluded:
        if row["path"] not in used:
            by_class.setdefault(row["class"], []).append(row["path"])
    rng = random.Random(args.round)
    for cls, k in (("generated", 2), ("third_party", 1), ("unbuilt", 1)):
        pool = sorted(by_class.get(cls, []))
        rng.shuffle(pool)
        take(pool, k, "adversarial: excluded (%s)" % cls, b)
    half = len(paths) // 2
    for out, pool in ((a, paths[:half]), (b, paths[half:])):
        pool = [p for p in pool if p not in picked and p not in used]
        rng.shuffle(pool)
        take(pool, 10, "random", out)
    folder = os.path.join(args.depmap, "review", "round%d" % args.round)
    os.makedirs(folder, exist_ok=True)
    for who, rows in (("A", a), ("B", b)):
        with open(os.path.join(folder, "sample-%s.txt" % who), "w") as handle:
            for p, category in rows:
                handle.write("%s\t%s\n" % (p, category))
        print("reviewer %s: %d files" % (who, len(rows)))
        for p, category in rows:
            print("  %-90s %s" % (p, category))


if __name__ == "__main__":
    main()
