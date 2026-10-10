-- Engine-neutral setup the parity judge runs before any case, on the original
-- and on the port alike. Derived from tools/deploy/init.sql and init_user.sql,
-- keeping only standard MySQL statements. The engine-specific part of those
-- files (ob_query_timeout, recyclebin, hidden _parameters, compaction knobs,
-- set_tp error injection, ANALYZE on internal virtual tables) is never applied
-- by the judge; see migration/judge/README.md.
create user if not exists 'admin' identified by 'admin';
create database if not exists test;
grant all on *.* to 'admin' with grant option;
