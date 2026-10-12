#!/bin/sh
# Phase 1c, sweep set S3b: c06 c07 c08 c09 c15 sweeps (+ the decided shard migration/inventory.c06-int-returns.tsv)
# and the name tables migration/design/{overloads,slots,qa_signatures,symbols}.tsv.
# Deterministic (same inputs -> byte-identical outputs); read-only on src/ and build_release/.
# Inputs: build_release/inventory-ast/decls.jsonl (ast_extract.py), migration/design/units.tsv (make_units.py),
#         migration/depmap/nodes.tsv, build_release/inventory-ast/files.tsv.
# Order: make_overloads.py before sweep_c15_overloads.py and make_symbols.py (both reuse its renames; they
# recompute them in-process, so the order only matters for reading the files side by side).
# Run from anywhere:  sh migration/scripts/inventory/run_sweeps_s3b.sh
set -e
cd "$(dirname "$0")/../../.."
for s in c06_int_returns c07_locks c08_atomics c09_layout; do
  python3 -I "migration/scripts/inventory/sweep_$s.py"
done
python3 -I migration/scripts/inventory/make_overloads.py
python3 -I migration/scripts/inventory/sweep_c15_overloads.py
python3 -I migration/scripts/inventory/make_slots.py
python3 -I migration/scripts/inventory/make_qa_signatures.py
python3 -I migration/scripts/inventory/make_symbols.py
