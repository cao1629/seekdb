
#--echo
#--echo ########### import srs data ###########

# judge rewrite (b1-geometry) of
# tools/deploy/mysql_test/test_suite/geometry/t/import_default_srs_data_mysql.inc.
# Only change: the SRS count is read from the standard view
# information_schema.ST_SPATIAL_REFERENCE_SYSTEMS (one row per SRS) instead of
# the raw internal table oceanbase.__all_spatial_reference_systems.
# The load below is a non-asserting fixture, kept unchanged: it is the same
# REPLACE INTO path the packaged tools/import_srs_data.py uses, and SQL has no
# public statement to define an SRS. See rewrites/b1-geometry.tsv.

--disable_query_log
--disable_result_log
--disable_warnings

connect (conn_admin, $OBMYSQL_MS0,admin,$OBMYSQL_PWD,test,$OBMYSQL_PORT);
connection conn_admin;
let $c1 = query_get_value(select count(*) as cnt from information_schema.ST_SPATIAL_REFERENCE_SYSTEMS, cnt, 1);
# TODO@dazhi: import to mysql tenant
#alter system change tenant sys ;
#sleep 2;

if ($c1 != 5160)
{
  --source mysql_test/test_suite/geometry/t/default_srs_data_mysql.sql
  --exec sleep 30
}

# TODO@dazhi: use mysql tenant
#disconnect conn_admin;
#connection default;

--enable_query_log
--enable_result_log
--enable_warnings
