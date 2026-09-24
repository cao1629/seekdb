# Evidence behind the design document

The scripts, counts and intermediate files the Step 1 design agents produced (workflow wf_9fd55a59-9bf,
2026-09-24/25), moved here from the job's scratch directory so the design document's references
resolve. Paths inside the design files point here; scripts that wrote to the scratch directory now
write here.

Left out, because they are caches or can be regenerated:
- `nmcache.pkl` (59 MB): a pickle of the symbol tables the link-level scripts read.
- `01-crates/nm/all.txt` (59 MB): `nm -m -g` over every `.o` under
  /Users/colin/seekdb-dev/ref-834bbee1e/build_release/src; the command is in
  research/01-crates-core.md.
- `01-crates/out-raw/` and `01-crates/out-home/`: two earlier runs of the graph script, each a
  3.4 MB `graph.json` like the one kept in `01-crates/out/`.

Kept for review: `rulebook/backup/` (the design files before the integration step's edits, see
RESOLUTIONS.md) and `review-1/orig/` (the design files before the review revision, see REVIEW-1.md).
