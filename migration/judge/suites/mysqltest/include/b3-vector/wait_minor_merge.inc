# Judge rewrite (batch b3-vector) of mysql_test/include/wait_minor_merge.inc.
# The original polls oceanbase.__all_virtual_tablet_memstore_info, an internal
# virtual table (Tier B), until no frozen memstore is left, for up to 180 s.
# No public interface exposes memstore state, so this version only gives the
# preceding ALTER SYSTEM MINOR FREEZE a fixed time to settle. Like the original
# on success, it prints nothing. Callers follow it with ALTER SYSTEM MAJOR
# FREEZE plus mysql_test/include/wait_daily_merge.inc, which waits on the
# DBA_OB_MAJOR_COMPACTION dictionary view (gray zone), so query results never
# depend on this wait.

--disable_query_log
--disable_result_log
--exec sleep 10
--enable_query_log
--enable_result_log
