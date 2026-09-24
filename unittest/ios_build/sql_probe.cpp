// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "sql_probe.h"
#include "seekdb_ios.h"
#include "observer/ob_server.h"
#include "common/mysqlclient/ob_mysql_proxy.h"
#include "common/mysqlclient/ob_mysql_result.h"
#include "common/mysqlclient/ob_mysql_transaction.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "share/rc/ob_server_runtime.h"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using namespace oceanbase;
using namespace oceanbase::common;

namespace {
using Rows = std::vector<std::vector<std::string>>;

/** Sentinel used only when a successful statement has no stable affected-row contract. */
constexpr int64_t IGNORE_AFFECTED_ROWS = -1;

/** Execute generic SQL fixtures and retain only the first failure. */
class Suite {
public:
  /** Borrow the engine proxy and an optional evidence stream for one suite execution. */
  Suite(ObMySQLProxy &proxy, FILE *report) : proxy_(proxy), report_(report) {}

  /** Roll back an unfinished transaction, including after an assertion failure. */
  ~Suite()
  {
    if (transaction_.is_started()) {
      transaction_.end(false);
    }
  }

  /** Return the first database, assertion, or evidence-write error. */
  int result() const { return result_; }

  /** Execute SQL and require an exact affected-row count unless the explicit ignore sentinel is provided. */
  void write(const char *name, const char *sql, int64_t expected_affected, int expected_error = OB_SUCCESS)
  {
    if (result_ != OB_SUCCESS) {
      return;
    }
    int64_t actual_affected = 0;
    int ret = client().write(sql, actual_affected);
    if (ret == expected_error) {
      if (expected_error == OB_SUCCESS && expected_affected != IGNORE_AFFECTED_ROWS &&
          actual_affected != expected_affected) {
        ret = OB_ERR_UNEXPECTED;
      } else {
        ret = OB_SUCCESS;
      }
    } else {
      ret = OB_ERR_UNEXPECTED;
    }
    record(name, ret);
  }

  /** Check exact rows and columns using only signed, unsigned, and string cell prefixes. */
  void read(const char *name, const char *sql, const Rows &expected)
  {
    if (result_ != OB_SUCCESS) {
      return;
    }
    int ret = validate_cells(expected);
    ObISQLClient::ReadResult result;
    if (ret == OB_SUCCESS) {
      ret = client().read(result, sql);
    }
    auto *rows = ret == OB_SUCCESS ? result.get_result() : nullptr;
    if (ret == OB_SUCCESS && rows == nullptr) {
      ret = OB_ERR_UNEXPECTED;
    }
    for (const auto &row : expected) {
      if (ret != OB_SUCCESS) {
        break;
      }
      ret = rows->next();
      if (ret == OB_SUCCESS && rows->get_column_count() != static_cast<int64_t>(row.size())) {
        ret = OB_ERR_UNEXPECTED;
      }
      for (size_t column = 0; ret == OB_SUCCESS && column < row.size(); ++column) {
        ret = check_cell(*rows, static_cast<int64_t>(column), row[column]);
      }
    }
    if (ret == OB_SUCCESS && rows->next() != OB_ITER_END) {
      ret = OB_ERR_UNEXPECTED;
    }
    record(name, ret);
  }

  /** Read exactly one signed integer cell and return it to the caller. */
  void read_signed(const char *name, const char *sql, int64_t &value)
  {
    if (result_ != OB_SUCCESS) {
      return;
    }
    ObISQLClient::ReadResult result;
    int ret = client().read(result, sql);
    auto *rows = ret == OB_SUCCESS ? result.get_result() : nullptr;
    if (ret == OB_SUCCESS && rows == nullptr) {
      ret = OB_ERR_UNEXPECTED;
    }
    if (ret == OB_SUCCESS) {
      ret = rows->next();
    }
    if (ret == OB_SUCCESS && rows->get_column_count() != 1) {
      ret = OB_ERR_UNEXPECTED;
    }
    if (ret == OB_SUCCESS) {
      ret = rows->get_int(static_cast<int64_t>(0), value);
    }
    if (ret == OB_SUCCESS && rows->next() != OB_ITER_END) {
      ret = OB_ERR_UNEXPECTED;
    }
    record(name, ret);
  }

  /** Start a transaction that pins subsequent statements to one connection. */
  void begin(const char *name)
  {
    if (result_ == OB_SUCCESS) {
      record(name, transaction_.start(&proxy_));
    }
  }

  /** End the active transaction with either commit or rollback semantics. */
  void end(const char *name, bool commit)
  {
    if (result_ == OB_SUCCESS) {
      record(name, transaction_.end(commit));
    }
  }

private:
  /** Reject malformed or unsupported expected-cell encodings before issuing SQL. */
  static int validate_cells(const Rows &expected)
  {
    for (const auto &row : expected) {
      for (const auto &cell : row) {
        if (cell.compare(0, 2, "i:") != 0 && cell.compare(0, 2, "u:") != 0 &&
            cell.compare(0, 2, "s:") != 0) {
          return OB_INVALID_ARGUMENT;
        }
      }
    }
    return OB_SUCCESS;
  }

  /** Parse an expected signed integer and reject incomplete or overflowing encodings. */
  static int parse_signed(const std::string &text, int64_t &value)
  {
    errno = 0;
    char *end = nullptr;
    const char *begin = text.c_str() + 2;
    const long long parsed = std::strtoll(begin, &end, 10);
    if (errno == ERANGE || end == begin || *end != '\0') {
      return OB_INVALID_ARGUMENT;
    }
    value = static_cast<int64_t>(parsed);
    return OB_SUCCESS;
  }

  /** Parse an expected unsigned integer and reject signs, overflow, or incomplete encodings. */
  static int parse_unsigned(const std::string &text, uint64_t &value)
  {
    const char *begin = text.c_str() + 2;
    if (*begin == '-' || *begin == '+') {
      return OB_INVALID_ARGUMENT;
    }
    errno = 0;
    char *end = nullptr;
    const unsigned long long parsed = std::strtoull(begin, &end, 10);
    if (errno == ERANGE || end == begin || *end != '\0') {
      return OB_INVALID_ARGUMENT;
    }
    value = static_cast<uint64_t>(parsed);
    return OB_SUCCESS;
  }

  /** Compare one result cell against its strictly typed expected encoding. */
  static int check_cell(sqlclient::ObMySQLResult &rows, int64_t column, const std::string &expected)
  {
    int ret = OB_SUCCESS;
    if (expected.compare(0, 2, "i:") == 0) {
      int64_t actual = 0;
      int64_t wanted = 0;
      if ((ret = parse_signed(expected, wanted)) == OB_SUCCESS &&
          (ret = rows.get_int(column, actual)) == OB_SUCCESS && actual != wanted) {
        ret = OB_ERR_UNEXPECTED;
      }
    } else if (expected.compare(0, 2, "u:") == 0) {
      uint64_t actual = 0;
      uint64_t wanted = 0;
      if ((ret = parse_unsigned(expected, wanted)) == OB_SUCCESS &&
          (ret = rows.get_uint(column, actual)) == OB_SUCCESS && actual != wanted) {
        ret = OB_ERR_UNEXPECTED;
      }
    } else if (expected.compare(0, 2, "s:") == 0) {
      ObString actual;
      ret = rows.get_varchar(column, actual);
      const std::string value(actual.empty() ? "" : actual.ptr(), actual.length());
      if (ret == OB_SUCCESS && value != expected.substr(2)) {
        ret = OB_ERR_UNEXPECTED;
      }
    } else {
      ret = OB_INVALID_ARGUMENT;
    }
    return ret;
  }

  /** Select the pinned transactional client while a transaction is active. */
  ObISQLClient &client()
  {
    return transaction_.is_started() ? static_cast<ObISQLClient &>(transaction_) : proxy_;
  }

  /** Record and flush one completed step so a later crash cannot erase its evidence. */
  void record(const char *name, int ret)
  {
    result_ = ret;
    ++step_;
    if (report_ != nullptr &&
        (std::fprintf(report_, "{\"step\":%d,\"case\":\"%s\",\"result\":%d}\n", step_, name, ret) < 0 ||
         std::fflush(report_) != 0)) {
      result_ = OB_IO_ERROR;
    }
  }

  ObMySQLProxy &proxy_;
  ObMySQLTransaction transaction_;
  FILE *report_;
  int result_ = OB_SUCCESS;
  int step_ = 0;
};

/** Create product-neutral tables and clear only the per-run fixture tables. */
void schema_cases(Suite &suite)
{
  suite.write("schema.database", "CREATE DATABASE IF NOT EXISTS ios_probe", IGNORE_AFFECTED_ROWS);
  suite.write("schema.lifecycle",
              "CREATE TABLE IF NOT EXISTS ios_probe.lifecycle (id INT PRIMARY KEY, runs BIGINT NOT NULL)",
              IGNORE_AFFECTED_ROWS);
  suite.write("schema.feature_matrix", R"SQL(CREATE TABLE IF NOT EXISTS ios_probe.feature_matrix (
    feature_id VARCHAR(128) COLLATE utf8mb4_bin PRIMARY KEY,
    unique_name VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    version BIGINT UNSIGNED NOT NULL,
    payload JSON NOT NULL,
    binary_value MEDIUMBLOB NOT NULL,
    string_values VARCHAR(256) [] NOT NULL,
    optional_items JSON,
    source_text TEXT,
    translated_text TEXT,
    UNIQUE KEY uk_feature_matrix_name (unique_name),
    CONSTRAINT ck_feature_matrix_version CHECK (version >= 1),
    CONSTRAINT ck_feature_matrix_optional_items CHECK (optional_items IS NULL OR JSON_TYPE(optional_items) = 'ARRAY'),
    CONSTRAINT ck_feature_matrix_text_pair CHECK ((source_text IS NULL AND translated_text IS NULL) OR
                                                  (source_text IS NOT NULL AND translated_text IS NOT NULL))
  ) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin)SQL", IGNORE_AFFECTED_ROWS);
  suite.write("schema.feature_event", R"SQL(CREATE TABLE IF NOT EXISTS ios_probe.feature_event (
    event_id VARCHAR(128) COLLATE utf8mb4_bin PRIMARY KEY,
    feature_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    payload JSON NOT NULL
  ) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin)SQL", IGNORE_AFFECTED_ROWS);
  suite.write("fixture.clear_events", "DELETE FROM ios_probe.feature_event", IGNORE_AFFECTED_ROWS);
  suite.write("fixture.clear_matrix", "DELETE FROM ios_probe.feature_matrix", IGNORE_AFFECTED_ROWS);
}

/** Verify expression evaluation and increment the persistent lifecycle counter. */
void lifecycle_cases(Suite &suite, int64_t &previous_runs)
{
  suite.read("expression.answer", "SELECT 6 * 7", {{"i:42"}});
  suite.read_signed("lifecycle.previous", "SELECT COALESCE(MAX(runs), 0) FROM ios_probe.lifecycle WHERE id=1",
                    previous_runs);
  const int64_t expected_affected = previous_runs == 0 ? 1 : 2;
  suite.write("lifecycle.increment",
              "INSERT INTO ios_probe.lifecycle VALUES (1,1) ON DUPLICATE KEY UPDATE runs=runs+1", expected_affected);
  suite.read("lifecycle.current", "SELECT runs FROM ios_probe.lifecycle WHERE id=1",
             {{"i:" + std::to_string(previous_runs + 1)}});
}

/** Exercise binary keys, JSON, BLOB, unsigned integers, arrays, and unique atomicity. */
void value_cases(Suite &suite)
{
  suite.write("binary.insert", R"SQL(INSERT INTO ios_probe.feature_matrix
      (feature_id,unique_name,version,payload,binary_value,string_values,optional_items,source_text,translated_text)
    VALUES ('Key','Alpha',1,'{"kind":"upper"}',X'00017FFF',
            CONVERT(X'5b226f6e65222c22e4b8ade69687225d' USING utf8mb4),'["first"]','hello','你好'),
           ('key','alpha',2,'{"kind":"lower"}',X'02','[]',NULL,NULL,NULL))SQL", 2);
  suite.read("binary.case_sensitive", "SELECT feature_id FROM ios_probe.feature_matrix WHERE feature_id='Key'", {{"s:Key"}});
  suite.read("binary.order", "SELECT feature_id FROM ios_probe.feature_matrix ORDER BY feature_id", {{"s:Key"}, {"s:key"}});
  suite.read("json.extract", "SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.kind')) FROM ios_probe.feature_matrix "
                             "WHERE feature_id='Key'", {{"s:upper"}});
  suite.read("blob.round_trip", "SELECT HEX(binary_value) FROM ios_probe.feature_matrix WHERE feature_id='Key'",
             {{"s:00017FFF"}});
  suite.read("unsigned.read", "SELECT version FROM ios_probe.feature_matrix WHERE feature_id='key'", {{"u:2"}});
  suite.read("array.round_trip", "SELECT array_to_string(string_values,CONVERT(X'1f' USING utf8mb4),"
                                 "CONVERT(X'00' USING utf8mb4)),JSON_TYPE(optional_items) "
                                 "FROM ios_probe.feature_matrix WHERE feature_id='Key'",
             {{std::string("s:one") + char(31) + "中文", "s:ARRAY"}});
  suite.write("unique.duplicate_batch", R"SQL(INSERT INTO ios_probe.feature_matrix
      (feature_id,unique_name,version,payload,binary_value,string_values)
    VALUES ('new','New',1,'{}',X'','[]'),('duplicate','Alpha',1,'{}',X'','[]'))SQL",
              0, OB_ERR_PRIMARY_KEY_DUPLICATE);
  suite.read("unique.no_partial_insert", "SELECT feature_id FROM ios_probe.feature_matrix WHERE feature_id IN ('new','duplicate')",
             {});
}

/** Verify explicit rollback removes events and restores the modified feature row. */
void rollback_cases(Suite &suite)
{
  suite.begin("rollback.begin");
  suite.write("rollback.insert_event", "INSERT INTO ios_probe.feature_event VALUES ('rollback','Key','{\"state\":\"discard\"}')", 1);
  suite.write("rollback.update_state", "UPDATE ios_probe.feature_matrix SET version=3 WHERE feature_id='Key'", 1);
  suite.end("rollback.end", false);
  suite.read("rollback.event_absent", "SELECT event_id FROM ios_probe.feature_event WHERE event_id='rollback'", {});
  suite.read("rollback.state_restored", "SELECT version FROM ios_probe.feature_matrix WHERE feature_id='Key'", {{"u:1"}});
}

/** Verify row locking, optimistic commit, and rejection of a stale version update. */
void optimistic_cases(Suite &suite)
{
  suite.begin("optimistic.begin");
  suite.read("optimistic.lock", "SELECT version FROM ios_probe.feature_matrix WHERE feature_id='Key' FOR UPDATE", {{"u:1"}});
  suite.write("optimistic.insert_event", "INSERT INTO ios_probe.feature_event VALUES ('commit','Key','{\"state\":\"saved\"}')", 1);
  suite.write("optimistic.update", "UPDATE ios_probe.feature_matrix SET version=2 WHERE feature_id='Key' AND version=1", 1);
  suite.end("optimistic.commit", true);
  suite.read("optimistic.readback", "SELECT m.version,JSON_UNQUOTE(JSON_EXTRACT(e.payload,'$.state')) "
                                    "FROM ios_probe.feature_matrix m JOIN ios_probe.feature_event e "
                                    "ON m.feature_id=e.feature_id WHERE m.feature_id='Key' AND e.event_id='commit'",
             {{"u:2", "s:saved"}});
  suite.write("optimistic.stale", "UPDATE ios_probe.feature_matrix SET version=3 WHERE feature_id='Key' AND version=1", 0);
}

/** Require three CHECK failures and prove that every rejected update preserved row state. */
void constraint_cases(Suite &suite)
{
  suite.write("check.version", "UPDATE ios_probe.feature_matrix SET version=0 WHERE feature_id='Key'", 0,
              OB_ERR_CHECK_CONSTRAINT_VIOLATED);
  suite.write("check.json_array", "UPDATE ios_probe.feature_matrix SET optional_items='{}' WHERE feature_id='Key'", 0,
              OB_ERR_CHECK_CONSTRAINT_VIOLATED);
  suite.write("check.text_pair", "UPDATE ios_probe.feature_matrix SET translated_text=NULL WHERE feature_id='Key'", 0,
              OB_ERR_CHECK_CONSTRAINT_VIOLATED);
  suite.read("check.state_preserved", "SELECT version,JSON_TYPE(optional_items),source_text,translated_text "
                                      "FROM ios_probe.feature_matrix WHERE feature_id='Key'",
             {{"u:2", "s:ARRAY", "s:hello", "s:你好"}});
}
}

/** Run generic SQL fixtures and persist per-step evidence when a path is provided. */
int seekdb_ios_probe_sql(const char *report_path, int64_t *previous_runs)
{
  if (previous_runs == nullptr || (report_path != nullptr && report_path[0] != '/') ||
      seekdb_ios_get_state() != SEEKDB_IOS_RUNNING) {
    return OB_INVALID_ARGUMENT;
  }
  FILE *report = report_path == nullptr ? nullptr : std::fopen(report_path, "w");
  if (report_path != nullptr && report == nullptr) {
    return OB_IO_ERROR;
  }
  lib::ObStackHeaderGuard stack_header;
  lib::Worker worker;
  lib::Worker::set_worker_to_thread_local(&worker);
  int ret = OB_ERR_UNEXPECTED;
  SERVER_MODULE_SCOPE {
    Suite suite(observer::ObServer::get_instance().get_mysql_proxy(), report);
    schema_cases(suite);
    lifecycle_cases(suite, *previous_runs);
    value_cases(suite);
    rollback_cases(suite);
    optimistic_cases(suite);
    constraint_cases(suite);
    ret = suite.result();
  }
  lib::Worker::set_worker_to_thread_local(nullptr);
  if (report != nullptr) {
    if ((std::fprintf(report, "{\"complete\":true,\"result\":%d}\n", ret) < 0 || std::fflush(report) != 0) &&
        ret == OB_SUCCESS) {
      ret = OB_IO_ERROR;
    }
    if (std::fclose(report) != 0 && ret == OB_SUCCESS) {
      ret = OB_IO_ERROR;
    }
  }
  return ret;
}
