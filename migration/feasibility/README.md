# Feasibility evidence

Scripts and compact results behind `migration/00-feasibility-report.md`. Every script is read-only toward the repo (stdlib Python or plain shell; no builds, no test runs); point it at a seekdb checkout with `REPO=/path/to/seekdb` and run Python with `python3 -I`. Large intermediate outputs (comment-stripped source mirror, full include-edge list, CI logs) were not kept.

These are survey artifacts, not Step 1 artifacts: in particular `scripts/units/depmap_seekdb.py` is a starting point for `prompts/01-dependency-map.md`, which must still pass its two-reviewer verification rounds before the map it produces is trusted.

| Area | Scripts | Results |
|---|---|---|
| Pain census (fix commits by bug class) | `scripts/pain/` | `results/pain/verdicts.tsv` |
| Architecture (ownership, polymorphism, unsafe share) | `scripts/arch/` | `results/arch/` (`gen.ignore` lists the generated files excluded) |
| Test census (portable vs internal-bound mysqltest cases) | `scripts/tests/` | `results/tests/` (`classification.json` is per case; `mt_ci_*.txt` are the CI-run lists) |
| Build baseline (CI timings) | `scripts/build/` | `results/build/` |
| Units of work, generated code, dependency map | `scripts/units/` | `results/units/` (`depmap/report.txt` summarizes the include graph; `size/` has per-module sizes) |
