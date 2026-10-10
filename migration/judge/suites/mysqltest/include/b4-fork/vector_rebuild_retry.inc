# Judge helper (batch b4-fork): run one dbms_vector.rebuild_index call and
# retry it while it fails with 4179 (OB_OP_NOT_ALLOW, "Calling dbms_vector.refresh
# when other refresh/rebuild tasks may be running is not allowed").
# Rules: migration/judge/README.md, section "改写规则".
#
# Why: in the original fork_table_vector, every rebuild ran only after
# wait_fork_table.inc had polled the internal oceanbase.__all_ddl_task_status
# until the preceding FORK TABLE's background task was gone. Until then that
# task holds the fork's locks on the source and the target, and the user-facing
# rebuild does not wait for them: it fails at once with 4179. The fork task has
# no public completion signal (see wait_fork_table.inc), so the judge rewrite
# retries the call itself: up to $judge_rebuild_tries attempts (default 120)
# with a 1 s wait (--exec sleep 1, the judge's wait form) between attempts.
# On the original binary a fork task can sit up to ~25 s between its freeze and
# build stages (waiting for the source's memtables to be flushed), so a rebuild
# needed up to ~25 attempts in validation; 120 leaves margin on a loaded host.
# A 4179 attempt is rolled back by the engine and leaves no trace. The attempts
# run with query and result logging disabled so their number never reaches the
# .result; one echo line then records the statement. If the last attempt still
# fails, the case dies.
#
# PARAMETERS
#   $judge_rebuild_stmt  - the complete CALL statement, without the final ';'
#   $judge_rebuild_tries - optional attempt limit (default 120); reset after use

--disable_query_log
--disable_result_log

let $judge_rebuild_left = 120;
if ($judge_rebuild_tries)
{
  let $judge_rebuild_left = $judge_rebuild_tries;
}
let $judge_rebuild_errno = 4179;
while ($judge_rebuild_left)
{
  --error 0,4179
  eval $judge_rebuild_stmt;
  let $judge_rebuild_errno = $mysql_errno;
  dec $judge_rebuild_left;
  if (!$judge_rebuild_errno)
  {
    let $judge_rebuild_left = 0;
  }
  if ($judge_rebuild_errno)
  {
    --exec sleep 1
  }
}

--enable_query_log
--enable_result_log

if ($judge_rebuild_errno)
{
  --echo # judge: $judge_rebuild_stmt still failed with $judge_rebuild_errno after the last attempt
  die dbms_vector.rebuild_index kept failing with 4179;
}
--echo # judge: $judge_rebuild_stmt succeeded (retried while it returned 4179)
let $judge_rebuild_tries = 0;
