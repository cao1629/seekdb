# Fork Table 专用的等待 Major Freeze 完成脚本
#
# Judge rewrite (batch b4-fork) of
#   mysql_test/test_suite/fork_table/include/wait_daily_merge.inc
# Rules: migration/judge/README.md, section "改写规则".
#
# Unchanged: the admin connection waits until oceanbase.DBA_OB_MAJOR_COMPACTION
# reports frozen_scn == last_scn (gray zone: DBA_OB_* view, kept), and prints
# "major freeze failed" plus a DBA_OB_MAJOR_COMPACTION dump on timeout.
# Removed: the timeout-only diagnostics that read the internal tables
# oceanbase.__all_virtual_server_compaction_event_history and
# oceanbase.__all_virtual_compaction_diagnose_info (Tier B), and the two lets
# ($__tenant_id__, $__frozen_scn__ from DBA_OB_MAJOR_COMPACTION) that only fed
# them. They never ran in a passing case; a timeout still fails the case through
# the "major freeze failed" line.

--disable_query_log
--disable_result_log
connect (obsys_wait_merge,$OBMYSQL_MS0,admin,$OBMYSQL_PWD,test,$OBMYSQL_PORT);
connection obsys_wait_merge;

let $__i__= 1200;
--exec sleep 1
while($__i__ > 0)
{
    --exec sleep 1
    dec $__i__;
    let $__frozen_scn__ = query_get_value(select /*+query_timeout(100000000)*/sum(frozen_scn/1000) as sum_value from oceanbase.DBA_OB_MAJOR_COMPACTION, sum_value, 1);
    let $__merged_version__ = query_get_value(select /*+query_timeout(100000000)*/sum(last_scn/1000) as sum_value from oceanbase.DBA_OB_MAJOR_COMPACTION, sum_value, 1);
    if($__frozen_scn__ == $__merged_version__)
    {
        let $__i__ = -5;
    }
}

if($__i__ != -5)
{
    --echo major freeze failed
    select * from oceanbase.DBA_OB_MAJOR_COMPACTION;
}
--exec sleep 1

disconnect obsys_wait_merge;
connection default;
--enable_query_log
--enable_result_log

