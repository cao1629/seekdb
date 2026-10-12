#!/bin/sh
# Phase 1c, sweep set S4: the RULEBOOK 0.4 seam table.
#   writes migration/design/seams.tsv, migration/inventory/sweep/seams-summary.tsv and the `seams` row of
#   migration/inventory/sweep/counts.tsv; prints the reconciliation with the depmap's 741 cycle symbols.
# Needs: build_release/ object files (nm, macOS Mach-O format; DEV-007), build_release/inventory-ast/decls.jsonl
# (ast_extract.py), migration/design/units.tsv and the c13 outputs (run_sweeps_s2.sh).
# Deterministic (same inputs -> byte-identical outputs); read-only on src/ and build_release/; about 30 s.
# Run from anywhere:  sh migration/scripts/inventory/run_seams.sh
set -e
cd "$(dirname "$0")/../../.."
python3 -I migration/scripts/inventory/make_seams.py "$@"
