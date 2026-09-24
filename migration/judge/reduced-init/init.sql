create user if not exists 'admin' IDENTIFIED BY 'admin';
create database if not exists test;
grant all on *.* to 'admin' WITH GRANT OPTION;
