# Judge rewrite (batch b4-fork) of
#   mysql_test/test_suite/fork_table/include/wait_fork_table.inc
# Rules: migration/judge/README.md, section "改写规则".
#
# The original connected as root@sys and polled oceanbase.__all_ddl_task_status
# (ddl_type 10009 = DDL_FORK_TABLE) until the background fork task row
# disappeared (default mode), or until a task matched by internal table ids
# reached an internal stage ($wait_status mode). That table and the task stages
# are internal (Tier B).
#
# Fork completion is not observable through the public interface. When FORK
# TABLE returns, the target table already exists, is visible in
# information_schema and reads the fork snapshot (copy-on-write over the
# source). Only the original engine's background task, which builds the
# target's own data and then releases the fork's table locks, is still running,
# and no public view exposes it. This replacement therefore waits a fixed time:
# the wait is now timing-based. The time is chosen so that the background task
# normally finishes, keeping the following statements in the same state as with
# the original include. Reads and DML see the fork snapshot either way, and
# ordinary DDL on the source or target is not rejected because of a running
# fork; it waits for the fork's table locks (up to the DDL timeout). The
# original suite already issues DDL right after FORK TABLE without any wait
# (fork_table_partition section 6, fork_table_error cleanup). Exception:
# dbms_vector.rebuild_index does not wait and fails with 4179 while the fork
# task still holds the table; callers run it through vector_rebuild_retry.inc.
#
# Stage waits ($wait_status) only existed to pair with debug_sync holds. The
# judge rewrites removed every such caller together with its debug_sync, so the
# mode is rejected here.
#
# The original's input variables are still accepted and reset, as the original
# consumed them, so they cannot leak into later includes such as
# wait_condition.inc. Like the original, this include ends with query and
# result logging enabled and the default connection selected.

--disable_query_log
--disable_result_log

if ($wait_status)
{
  --echo # wait_status (fork task stage) is internal state; not supported by the judge rewrite of wait_fork_table.inc
  die wait_status is not supported by the judge rewrite of wait_fork_table.inc;
}

let $wait_timeout= 0;
let $wait_src_table_id= 0;
let $wait_dest_table_id= 0;
let $wait_task_cnt= 0;
let $wait_status= 0;

--exec sleep 3

--enable_query_log
--enable_result_log
connection default;
