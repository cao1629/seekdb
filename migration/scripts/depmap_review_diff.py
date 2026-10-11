#!/usr/bin/env python3
"""Diff a skeptic reviewer's rows against the dependency map, in both directions (prompts/01).

For every sampled file:
  missed     the reviewer found an EDGE (with file:line evidence) the map does not have
  invented   the map has an edge the reviewer's rows do not support
  exclusion  the map and the reviewer disagree on whether the file itself is excluded
             (generated / third-party / unbuilt), or on its class
A discrepancy is only a candidate: check it against the source line before
changing the script. Conditional-flag and edge-kind disagreements are listed as
notes, not discrepancies.

Usage: python3 -I migration/scripts/depmap_review_diff.py --round N [--depmap migration/depmap]
Reads DEPMAP/review/roundN/reviewer-{A,B}.tsv and sample-{A,B}.txt; writes DEPMAP/review/roundN/diff.tsv.
"""
import argparse
import collections
import os


def read_rows(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        header = next(handle).rstrip("\n").split("\t")
        rows = []
        for line in handle:
            if not line.strip():
                continue
            cells = line.rstrip("\n").split("\t")
            cells += [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, cells)))
        return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--depmap", default="migration/depmap")
    args = ap.parse_args()
    folder = os.path.join(args.depmap, "review", "round%d" % args.round)
    edges = collections.defaultdict(dict)
    for row in read_rows(os.path.join(args.depmap, "edges.tsv")):
        edges[row["from"]][row["to"]] = row
    excluded = {row["path"]: row["class"] for row in read_rows(os.path.join(args.depmap, "excluded.tsv"))}
    out = ["reviewer\tfile\tkind\ttarget\treviewer_evidence\tmap_evidence"]
    notes = []
    totals = collections.Counter()
    for who in ("A", "B"):
        rows_path = os.path.join(folder, "reviewer-%s.tsv" % who)
        if not os.path.exists(rows_path):
            out.append("%s\t-\tmissing\t-\t%s not found\t-" % (who, rows_path))
            continue
        with open(os.path.join(folder, "sample-%s.txt" % who)) as handle:
            sample = [line.split("\t")[0] for line in handle if line.strip()]
        rows = read_rows(rows_path)
        by_file = collections.defaultdict(list)
        for r in rows:
            by_file[r["file"].strip()].append(r)
        for f in sample:
            rs = by_file.get(f, [])
            totals[who + " files"] += 1
            totals[who + " rows"] += len(rs)
            if not rs:
                if f in excluded or edges.get(f):
                    out.append("%s\t%s\tno_rows\t-\treviewer returned no rows for this file\t%s" % (
                        who, f, "map excludes it: %s" % excluded[f] if f in excluded else "map has %d edges from it" % len(edges[f])))
                else:
                    notes.append("%s\t%s\tno rows and no map edges agree (no include directives)" % (who, f))
                continue
            file_excluded = [r for r in rs if r["verdict"].strip() == "FILE_EXCLUDED"]
            if f in excluded or file_excluded:
                if f in excluded and file_excluded:
                    notes.append("%s\t%s\texclusion agrees\tmap=%s\treviewer=%s" % (who, f, excluded[f], file_excluded[0]["note"][:160]))
                else:
                    out.append("%s\t%s\texclusion\t-\t%s\t%s" % (
                        who, f, file_excluded[0]["note"][:200] if file_excluded else "reviewer treats the file as a node",
                        "map excludes it: %s" % excluded[f] if f in excluded else "map treats it as a node"))
                continue
            seen = {}
            for r in rs:
                if r["verdict"].strip() == "EDGE" and r["resolved_to"].strip() not in ("", "-"):
                    seen.setdefault(r["resolved_to"].strip(), []).append(r)
            mine = edges.get(f, {})
            for target in sorted(set(seen) - set(mine)):
                ev = "; ".join("L%s %s %s" % (r["line"], r["directive"], r["note"]) for r in seen[target])
                out.append("%s\t%s\tmissed\t%s\t%s\t-" % (who, f, target, ev))
            for target in sorted(set(mine) - set(seen)):
                m = mine[target]
                rv = [r for r in rs if r["resolved_to"].strip() == target]
                ev = "; ".join("L%s %s %s %s" % (r["line"], r["directive"], r["verdict"], r["note"]) for r in rv) or "no row resolves to it"
                out.append("%s\t%s\tinvented\t%s\t%s\tkind=%s lines=%s conditional=%s" % (who, f, target, ev, m["kind"], m["lines"], m["conditional"]))
            for target in sorted(set(mine) & set(seen)):
                m = mine[target]
                rcond = "1" if all(r["conditional"].strip() == "1" for r in seen[target]) else "0"
                if rcond != m["conditional"]:
                    notes.append("%s\t%s\tconditional differs\t%s\treviewer=%s map=%s" % (who, f, target, rcond, m["conditional"]))
    with open(os.path.join(folder, "diff.tsv"), "w") as handle:
        handle.write("\n".join(out) + "\n")
    with open(os.path.join(folder, "notes.tsv"), "w") as handle:
        handle.write("\n".join(notes) + "\n")
    print("rows=%s discrepancies=%d notes=%d -> %s" % (dict(totals), len(out) - 1, len(notes), os.path.join(folder, "diff.tsv")))
    for line in out[1:]:
        print(line[:400])


if __name__ == "__main__":
    main()
