# [judge] b7-misc rewrite of tools/deploy/mysql_test/include/get_tenant_server.inc.
# [judge] The original read the server address from oceanbase.__all_virtual_server_stat (Tier B):
# [judge]   let $__svr_ip__ = `select svr_ip from oceanbase.__all_virtual_server_stat`;
# [judge]   let $__svr_port__ = `select svr_port from oceanbase.__all_virtual_server_stat`;
# [judge]   let $__inner_port__ = `select sql_port from oceanbase.__all_virtual_server_stat`;
# [judge] The judge runs one seekdb instance and hands mysqltest its SQL address as $OBMYSQL_MS0:$OBMYSQL_PORT,
# [judge] which is the address the original looked up, so the copy takes it from there.
# [judge] $__svr_port__ (the internal RPC port) is no longer set; no b7-misc case reads it.
# [judge] $__tenant_name__ is ignored, as it was in the original. Output is unchanged: the original printed nothing.
# 功能: 获取指定租户的ip&port
# 1.最好使用前sleep下，否则tenant_schema有可能还未刷到server上导致连接不上
# 2.如果分布于多个server，取unit_id最小的
# 用法:
# 1.设置待连接的tenant_name
# 2.source本文件
# 3.connect
# 模板:
# sleep 3;
#let $__tenant_name__ = 'sys';
# --source mysql_test/include/get_tenant_server.inc
# connect (conn1,$TE_SERVER_IP,root@tt1,,*NO-ONE*,$TE_SERVER_PORT);


--disable_query_log
--disable_result_log
connect (obsys,$OBMYSQL_MS0,admin,$OBMYSQL_PWD,test,$OBMYSQL_PORT);
connection obsys;

let $__svr_ip__ = $OBMYSQL_MS0;

let $__inner_port__ = $OBMYSQL_PORT;

let $TE_SERVER_IP = $__svr_ip__;
let $TE_SERVER_PORT = $__inner_port__;
disconnect obsys;
connection default;
--enable_query_log
--enable_result_log
