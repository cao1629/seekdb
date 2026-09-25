# seekdb Rust migration

@/Users/colin/repo/code-migration-kit-with-claude-code/CLAUDE.md
@AGENTS.md

This worktree (branch `migrate-to-rust`) is the Rust migration of seekdb. Its C++ source is the frozen base 834bbee1e.

## Where the migration stands

- Step 0 (feasibility) is done: migration/feasibility/feasibility.md, with the developer's answers in migration/decisions.md.
- 00b (judge setup, the kit's `prompts/00b-judge-setup.md`): the first sign-off is done (2026-09-25, migration/judge/00b-signoff.md); the second set (items 4, 6, 7, 8 and the families) is built offline and waits for its live checks and the second sign-off before Step 2a.
- Step 1 (design document, dependency map, inventory) runs alongside 00b (RULEBOOK.md DEV-005; decisions.md row 5a: speed over tokens, effort max on every agent, row 4b).
- migration/RULEBOOK.md becomes the design document in Step 1; its section 7 (Deviation log) is kept as it is.

## Which document decides

- migration/PLAN.md decides the order of work. It overrides the routing in the kit's CLAUDE.md, which would send a session to `prompts/00-feasibility.md` while any of migration/RULEBOOK.md, migration/depmap/ and migration/manifest.tsv is missing, and it overrides any skill's routing.
- migration/decisions.md wins over PLAN.md where the two disagree, and PLAN.md is then corrected.
- The next actions are in PLAN.md section 10, "Next actions".
