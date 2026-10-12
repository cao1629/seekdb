#!/bin/sh
# Phase 1c, sweep set S3a: c02 c03 c04 c05 c19 c20 c22 and migration/design/types.tsv.
# Deterministic (same inputs -> byte-identical outputs); read-only on src/ and build_release/.
# Inputs: build_release/inventory-ast/decls.jsonl (ast_extract.py), migration/design/units.tsv (make_units.py).
# Each script loads the declaration database itself (about 3 s, 1.5 GB); they run one after another.
# Run from anywhere:  sh migration/scripts/inventory/run_sweeps_s3a.sh
set -e
cd "$(dirname "$0")/../../.."
for s in c02_ptr_params c03_ptr_members c04_borrow_members c05_self_arena c19_multi_owner c20_vinherit_voidptr \
         c22_session; do
  python3 -I "migration/scripts/inventory/sweep_$s.py"
done
python3 -I migration/scripts/inventory/make_types.py
