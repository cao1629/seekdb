# [judge] b7-misc rewrite of tools/deploy/mysql_test/test_suite/histogram/include/conn_mysql_tenant.inc.
# [judge] Only change: source the b7-misc get_tenant_server.inc, which takes the server address from
# [judge] $OBMYSQL_MS0/$OBMYSQL_PORT instead of oceanbase.__all_virtual_server_stat (Tier B).
let $__tenant_name__ = 'sys';
--source ../../migration/judge/suites/mysqltest/include/b7-misc/get_tenant_server.inc
connect (conn_stat_test_sys_mysql, $TE_SERVER_IP, root@sys,,*NO-ONE*, $TE_SERVER_PORT);
connection conn_stat_test_sys_mysql;

use test;
