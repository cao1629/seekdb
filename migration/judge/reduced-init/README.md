# The reduced init profile

PLAN.md section 4, item 2. The judge's C++-against-Rust recordings of the plain-SQL cases, and the
Step 2a narrow run, use these two files instead of tools/deploy/init.sql and init_user.sql. Both
builds run under the same profile, and the C++ reference is recorded again under it; nothing is
compared with the checked-in .result files under this profile.

What it keeps: the `admin` user the runner logs in as (MYSQLTEST_USER / MYSQLTEST_PASSWORD in
.github/script/seekdb/mysqltest_for_seekdb.py), the `test` database, and the grant.

What it drops from tools/deploy/init.sql, and why:

| Dropped | Lines | Why |
|---|---|---|
| `alter system set_tp ...` (12 statements) | 22-33 | They switch on self-checks that change behavior in release builds; the narrow path has no tracepoints |
| `_nlj_batching_enabled`, `_enable_adaptive_compaction`, `_enable_var_assign_use_das`, `_enable_spf_batch_rescan`, `_max_px_workers_per_cpu` | 17, 20, 34-36 | Hidden tuning parameters the narrow path does not implement |
| `ob_compaction_schedule_interval`, `merger_check_interval` | 18-19 | Compaction scheduling; the narrow path is memtable-only |
| `recyclebin = 'on'` | 16 | Needs the recycle bin; with it off, DROP removes objects directly (both builds run the same way) |
| the `exec_sql` PL procedure | 38-54 | System-package-free profile; no configured case calls `exec_sql` (`git grep` over tools/deploy/mysql_test: 0 files) |
| session variables (`ob_query_timeout`, `@mysqltest_mode`) and `system sleep 5` | 1, 10-15 | Session settings end with the obclient session that runs init.sql; the runner waits for readiness itself |

What it drops from tools/deploy/init_user.sql: the `ANALYZE TABLE` of two internal virtual tables
(lines 5-6) and the sleep; the user and grant stay.

The profile is fixed at 00b's first sign-off and changes only by a recorded amendment followed by
recording the C++ reference again.
