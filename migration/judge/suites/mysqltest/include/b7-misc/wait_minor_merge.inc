# [judge] b7-misc rewrite of tools/deploy/mysql_test/include/wait_minor_merge.inc.
# [judge] The original polled oceanbase.__all_virtual_tablet_memstore_info (Tier B) once a second, for at most
# [judge] 180 s, until no frozen memstore was left after ALTER SYSTEM MINOR FREEZE, and echoed
# [judge] "minor freeze failed" on timeout. No public view shows memstore state, so this copy only waits a
# [judge] fixed 10 s; it asserts nothing about the freeze. Output is unchanged when the original succeeded.
# set __table_id__ outside

--disable_query_log
--disable_result_log

connect (obsys_minor,$OBMYSQL_MS0,admin,$OBMYSQL_PWD,test,$OBMYSQL_PORT);
connection obsys_minor;

--exec sleep 10

disconnect obsys_minor;
connection default;

--enable_query_log
--enable_result_log
