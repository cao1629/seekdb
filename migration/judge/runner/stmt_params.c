/*
 * Binary-protocol parameter binding probe for the parity judge.
 *
 * mysqltest --ps-protocol sends statement text without placeholders, so it never
 * exercises COM_STMT_EXECUTE parameter decoding. This client binds typed
 * parameters (signed and unsigned integers at their limits, decimal, double,
 * datetime/date/negative time, multibyte text, binary, NULL) through the
 * MariaDB Connector/C API shipped in deps (libobclnt), runs a prepared SELECT
 * with parameters in the WHERE clause, and prints everything as text so the
 * transcript can be diffed between the original and the port.
 *
 * Usage: stmt_params HOST PORT USER PASSWORD DATABASE
 */
#include <mysql.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NCOLS 14

static MYSQL *conn;

static void query(const char *sql)
{
  printf("> %s\n", sql);
  if (mysql_query(conn, sql) != 0) {
    printf("ERROR %u: %s\n", mysql_errno(conn), mysql_error(conn));
    return;
  }
  MYSQL_RES *res = mysql_store_result(conn);
  if (res == NULL) {
    return;
  }
  unsigned int n = mysql_num_fields(res);
  MYSQL_ROW row;
  while ((row = mysql_fetch_row(res)) != NULL) {
    unsigned long *len = mysql_fetch_lengths(res);
    for (unsigned int i = 0; i < n; i++) {
      if (row[i] == NULL) {
        printf("%sNULL", i ? "\t" : "");
      } else {
        printf("%s%.*s", i ? "\t" : "", (int)len[i], row[i]);
      }
    }
    printf("\n");
  }
  mysql_free_result(res);
}

static void set_time(MYSQL_TIME *t, enum enum_mysql_timestamp_type type, unsigned y, unsigned mo,
                     unsigned d, unsigned h, unsigned mi, unsigned s, unsigned long us, my_bool neg)
{
  memset(t, 0, sizeof(*t));
  t->time_type = type;
  t->year = y;
  t->month = mo;
  t->day = d;
  t->hour = h;
  t->minute = mi;
  t->second = s;
  t->second_part = us;
  t->neg = neg;
}

struct row_values {
  int id;
  signed char t;
  short s;
  int i;
  long long b;
  unsigned long long u;
  const char *d;
  double f;
  MYSQL_TIME dt, dd, tm;
  const char *vc;
  const unsigned char *bl;
  unsigned long bl_len;
  int n;
  my_bool n_null;
};

static void insert_row(MYSQL_STMT *st, struct row_values *r)
{
  MYSQL_BIND p[NCOLS];
  unsigned long d_len = strlen(r->d), vc_len = strlen(r->vc), bl_len = r->bl_len;
  memset(p, 0, sizeof(p));
  p[0].buffer_type = MYSQL_TYPE_LONG;       p[0].buffer = &r->id;
  p[1].buffer_type = MYSQL_TYPE_TINY;       p[1].buffer = &r->t;
  p[2].buffer_type = MYSQL_TYPE_SHORT;      p[2].buffer = &r->s;
  p[3].buffer_type = MYSQL_TYPE_LONG;       p[3].buffer = &r->i;
  p[4].buffer_type = MYSQL_TYPE_LONGLONG;   p[4].buffer = &r->b;
  p[5].buffer_type = MYSQL_TYPE_LONGLONG;   p[5].buffer = &r->u;   p[5].is_unsigned = 1;
  p[6].buffer_type = MYSQL_TYPE_NEWDECIMAL; p[6].buffer = (void *)r->d; p[6].buffer_length = d_len; p[6].length = &d_len;
  p[7].buffer_type = MYSQL_TYPE_DOUBLE;     p[7].buffer = &r->f;
  p[8].buffer_type = MYSQL_TYPE_DATETIME;   p[8].buffer = &r->dt;
  p[9].buffer_type = MYSQL_TYPE_DATE;       p[9].buffer = &r->dd;
  p[10].buffer_type = MYSQL_TYPE_TIME;      p[10].buffer = &r->tm;
  p[11].buffer_type = MYSQL_TYPE_STRING;    p[11].buffer = (void *)r->vc; p[11].buffer_length = vc_len; p[11].length = &vc_len;
  p[12].buffer_type = MYSQL_TYPE_BLOB;      p[12].buffer = (void *)r->bl; p[12].buffer_length = bl_len; p[12].length = &bl_len;
  p[13].buffer_type = MYSQL_TYPE_LONG;      p[13].buffer = &r->n; p[13].is_null = &r->n_null;
  if (mysql_stmt_bind_param(st, p) != 0 || mysql_stmt_execute(st) != 0) {
    printf("insert id=%d: ERROR %u: %s\n", r->id, mysql_stmt_errno(st), mysql_stmt_error(st));
  } else {
    printf("insert id=%d: ok affected=%llu\n", r->id, (unsigned long long)mysql_stmt_affected_rows(st));
  }
}

static void prepared_select(signed char t_max, long long id_min)
{
  const char *sql = "select id, t, s, i, b, u, d, f, dt, dd, tm, vc, hex(bl), n from stmt_params "
                    "where t <= ? and id >= ? order by id";
  printf("> prepared: %s  [t_max=%d id_min=%lld]\n", sql, t_max, id_min);
  MYSQL_STMT *st = mysql_stmt_init(conn);
  if (mysql_stmt_prepare(st, sql, strlen(sql)) != 0) {
    printf("prepare: ERROR %u: %s\n", mysql_stmt_errno(st), mysql_stmt_error(st));
    mysql_stmt_close(st);
    return;
  }
  MYSQL_BIND p[2];
  memset(p, 0, sizeof(p));
  p[0].buffer_type = MYSQL_TYPE_TINY;     p[0].buffer = &t_max;
  p[1].buffer_type = MYSQL_TYPE_LONGLONG; p[1].buffer = &id_min;
  if (mysql_stmt_bind_param(st, p) != 0 || mysql_stmt_execute(st) != 0) {
    printf("execute: ERROR %u: %s\n", mysql_stmt_errno(st), mysql_stmt_error(st));
    mysql_stmt_close(st);
    return;
  }
  MYSQL_BIND out[NCOLS];
  char buf[NCOLS][256];
  unsigned long len[NCOLS];
  my_bool is_null[NCOLS], error[NCOLS];
  memset(out, 0, sizeof(out));
  for (int c = 0; c < NCOLS; c++) {
    out[c].buffer_type = MYSQL_TYPE_STRING;
    out[c].buffer = buf[c];
    out[c].buffer_length = sizeof(buf[c]);
    out[c].length = &len[c];
    out[c].is_null = &is_null[c];
    out[c].error = &error[c];
  }
  if (mysql_stmt_bind_result(st, out) != 0 || mysql_stmt_store_result(st) != 0) {
    printf("result: ERROR %u: %s\n", mysql_stmt_errno(st), mysql_stmt_error(st));
    mysql_stmt_close(st);
    return;
  }
  int rc;
  while ((rc = mysql_stmt_fetch(st)) == 0 || rc == MYSQL_DATA_TRUNCATED) {
    for (int c = 0; c < NCOLS; c++) {
      if (is_null[c]) {
        printf("%sNULL", c ? "\t" : "");
      } else {
        printf("%s%.*s%s", c ? "\t" : "", (int)(len[c] < sizeof(buf[c]) ? len[c] : sizeof(buf[c])),
               buf[c], error[c] ? "[truncated]" : "");
      }
    }
    printf("\n");
  }
  if (rc != MYSQL_NO_DATA) {
    printf("fetch: ERROR %u: %s\n", mysql_stmt_errno(st), mysql_stmt_error(st));
  }
  mysql_stmt_close(st);
}

int main(int argc, char **argv)
{
  if (argc != 6) {
    fprintf(stderr, "usage: %s HOST PORT USER PASSWORD DATABASE\n", argv[0]);
    return 2;
  }
  conn = mysql_init(NULL);
  if (mysql_real_connect(conn, argv[1], argv[3], argv[4], argv[5], (unsigned)atoi(argv[2]), NULL, 0) == NULL) {
    printf("connect: ERROR %u: %s\n", mysql_errno(conn), mysql_error(conn));
    return 1;
  }
  mysql_set_character_set(conn, "utf8mb4");
  query("drop table if exists stmt_params");
  query("create table stmt_params (id int primary key, t tinyint, s smallint, i int, b bigint, "
        "u bigint unsigned, d decimal(20,6), f double, dt datetime(6), dd date, tm time, "
        "vc varchar(64), bl varbinary(64), n int)");

  const char *ins = "insert into stmt_params values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)";
  printf("> prepared: %s\n", ins);
  MYSQL_STMT *st = mysql_stmt_init(conn);
  if (mysql_stmt_prepare(st, ins, strlen(ins)) != 0) {
    printf("prepare: ERROR %u: %s\n", mysql_stmt_errno(st), mysql_stmt_error(st));
  } else {
    static const unsigned char bl1[] = {0x00, 0xff, 0x10}, bl3[] = {0x41};
    struct row_values r[3];
    memset(r, 0, sizeof(r));
    r[0].id = 1; r[0].t = -1; r[0].s = -32768; r[0].i = INT32_MIN; r[0].b = INT64_MIN;
    r[0].u = UINT64_MAX; r[0].d = "-12345.678901"; r[0].f = -1.5e300;
    set_time(&r[0].dt, MYSQL_TIMESTAMP_DATETIME, 2026, 1, 2, 3, 4, 5, 123456, 0);
    set_time(&r[0].dd, MYSQL_TIMESTAMP_DATE, 1999, 12, 31, 0, 0, 0, 0, 0);
    /* -838:59:59; the binary protocol carries TIME as days + an hour byte, so 34 days 22 h */
    set_time(&r[0].tm, MYSQL_TIMESTAMP_TIME, 0, 0, 34, 22, 59, 59, 0, 1);
    r[0].vc = "h\xc3\xa9llo \xe2\x9c\x93"; r[0].bl = bl1; r[0].bl_len = sizeof(bl1); r[0].n_null = 1;

    r[1].id = 2; r[1].t = 127; r[1].s = 32767; r[1].i = INT32_MAX; r[1].b = INT64_MAX;
    r[1].u = 0; r[1].d = "0.000001"; r[1].f = 3.25;
    set_time(&r[1].dt, MYSQL_TIMESTAMP_DATETIME, 1970, 1, 1, 0, 0, 1, 0, 0);
    set_time(&r[1].dd, MYSQL_TIMESTAMP_DATE, 2026, 2, 28, 0, 0, 0, 0, 0);
    set_time(&r[1].tm, MYSQL_TIMESTAMP_TIME, 0, 0, 0, 12, 34, 56, 0, 0);
    r[1].vc = ""; r[1].bl = bl3; r[1].bl_len = 0; r[1].n = 7;

    r[2].id = 3; r[2].t = -128; r[2].s = -1; r[2].i = -1; r[2].b = -1;
    r[2].u = 1; r[2].d = "99999999999999.999999"; r[2].f = 1e-300;
    set_time(&r[2].dt, MYSQL_TIMESTAMP_DATETIME, 9999, 12, 31, 23, 59, 59, 999999, 0);
    set_time(&r[2].dd, MYSQL_TIMESTAMP_DATE, 1000, 1, 1, 0, 0, 0, 0, 0);
    set_time(&r[2].tm, MYSQL_TIMESTAMP_TIME, 0, 0, 0, 0, 0, 0, 0, 0);
    r[2].vc = "x"; r[2].bl = bl3; r[2].bl_len = sizeof(bl3); r[2].n_null = 1;

    for (int k = 0; k < 3; k++) {
      insert_row(st, &r[k]);
    }
  }
  mysql_stmt_close(st);

  query("select id, t, s, i, b, u, d, f, dt, dd, tm, vc, hex(bl), n from stmt_params order by id");
  prepared_select(-1, 1);
  prepared_select(127, 2);
  prepared_select(-128, 0);
  mysql_close(conn);
  return 0;
}
