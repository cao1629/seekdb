#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
r"""Build migration/manifest.tsv from a depmap order.txt (stdlib only, deterministic).

Reads order.txt (one tab-separated batch of source file paths per line; `#`
lines are comments), derives each target path by applying the --sub
substitutions (repeatable, applied in order, simple string replacement), and
writes a TSV with header `source\ttarget`, preserving batch order —
dependencies first, so queue_runner.mjs hands out work in migration order.

Targets must come out matching the rulebook's naming rules (RULEBOOK.md §4) —
the --sub substitutions are how you encode those rules. If any derived target
equals its source (the substitutions didn't apply), the script refuses and
exits 1 rather than write a manifest that points targets back at sources.

Usage:
  python3 make_manifest.py --order migration/depmap/order.txt \
      --out migration/manifest.tsv --sub 'src_prefix=dst_prefix' --sub '.py=.ts'

Test: python3 scripts/make_manifest.py --order fixtures/python/expected_order.txt --out /tmp/manifest.tsv --sub 'pkg/=ts/' --sub '.py=.ts' && diff /tmp/manifest.tsv fixtures/python/expected_manifest.tsv
"""

import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True, type=Path,
                    help="depmap order.txt (batches of source paths, deps first)")
    ap.add_argument("--out", required=True, type=Path,
                    help="manifest TSV to write (header: source\\ttarget)")
    ap.add_argument("--sub", action="append", default=[], metavar="OLD=NEW",
                    help="substitution old=new, repeatable, applied in order")
    args = ap.parse_args()

    subs = []
    for s in args.sub:
        old, sep, new = s.partition("=")
        if not sep or not old:
            print(f"ERROR: --sub must look like 'old=new', got: {s!r}", file=sys.stderr)
            sys.exit(1)
        subs.append((old, new))

    rows = []
    for line in args.order.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        for source in line.split("\t"):
            if not source:
                continue
            target = source
            for old, new in subs:
                target = target.replace(old, new)
            if target == source:
                print(
                    f"ERROR: derived target equals source for {source!r} — the "
                    "--sub substitutions didn't apply. Refusing to write a "
                    "manifest whose targets point back at the sources.",
                    file=sys.stderr,
                )
                sys.exit(1)
            rows.append((source, target))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("source\ttarget\n")
        for source, target in rows:
            f.write(f"{source}\t{target}\n")
    print(f"wrote {args.out}: {len(rows)} units")


if __name__ == "__main__":
    main()
