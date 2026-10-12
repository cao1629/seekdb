#!/bin/sh
# Phase 1c, sweep set S2: units.tsv draft plus the script-swept categories c10 c11 c12 c13 c14 c16 c17
# c18 c21 c23. Deterministic (same inputs -> byte-identical outputs); read-only on src/ and build_release/.
# Order matters: make_units.py first (c13 reads migration/design/units.tsv for unit-disposition notes).
# Run from anywhere:  sh migration/scripts/inventory/run_sweeps_s2.sh
set -e
cd "$(dirname "$0")/../../.."
python3 -I migration/scripts/inventory/make_units.py
for s in c10_plan_writes c11_scopes c12_side_channels c13_weak c14_init c16_dead c17_bugs c18_inplace \
         c21_parse_tree c23_itemtype; do
  python3 -I "migration/scripts/inventory/sweep_$s.py"
done
