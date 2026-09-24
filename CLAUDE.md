# seekdb Rust migration

@/Users/colin/repo/code-migration-kit-with-claude-code/CLAUDE.md
@AGENTS.md

This worktree (branch `migrate-to-rust`) is the Rust migration of seekdb. Its C++ source is the frozen base 834bbee1e.

## Where the migration stands

- Step 0 (feasibility) is done: migration/feasibility/feasibility.md, with the developer's answers in migration/decisions.md.
- The current step is 00b (judge setup, the kit's `prompts/00b-judge-setup.md`).
- migration/RULEBOOK.md is still the kit's template with only its section 7 (Deviation log) filled; Step 1 writes the rest.

## Which document decides

- migration/PLAN.md decides the order of work. It overrides the routing in the kit's CLAUDE.md, which would send a session to `prompts/00-feasibility.md` while any of migration/RULEBOOK.md, migration/depmap/ and migration/manifest.tsv is missing, and it overrides any skill's routing.
- migration/decisions.md wins over PLAN.md where the two disagree, and PLAN.md is then corrected.
- The next actions are in PLAN.md section 10, "Next actions".
