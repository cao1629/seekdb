// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "device_test_registry.h"

#include "common/mysqlclient/ob_mysql_proxy.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "observer/ob_server.h"
#include "share/ob_errno.h"
#include "share/rc/ob_server_runtime.h"

#include <array>
#include <cstdint>
#include <string>

namespace seekdb::ios_test {
namespace {

using oceanbase::common::OB_ITER_END;
using oceanbase::common::OB_SUCCESS;
using oceanbase::common::ObISQLClient;
using oceanbase::observer::ObServer;

/** Retain the reviewed mysqltest transcript that defines this device case. */
constexpr std::array<const char *, 10> expected_transcript = {
    "drop table if exists t1;",
    "create table t1 (nr int ,b char(30),str char(10), primary key (nr));",
    "select count(*) from t1;", "count(*)", "0", "select * from t1;",
    "nr\tb\tstr", "select * from t1 limit 0;", "nr\tb\tstr", "drop table t1;"};

/** Retain exact affected-row expectations for every non-query statement. */
constexpr std::array<int64_t, 4> expected_affected_rows = {0, 0, 0, 0};
static_assert(expected_transcript.size() == 10,
              "the tracked empty_table result transcript must remain complete");

/** Execute one write and assert both its return code and affected-row count. */
bool write_exact(TestContext &context, ObISQLClient &client, const char *name,
                 const char *sql, int64_t expected_affected_rows)
{
  int64_t actual_affected_rows = -1;
  const int result = client.write(sql, actual_affected_rows);
  const bool succeeded = context.assert_equal(
      std::string(name) + ".result", OB_SUCCESS, result,
      "mysqltest write returned the expected engine status");
  const bool affected = succeeded && context.assert_equal(
      std::string(name) + ".affected_rows", expected_affected_rows,
      actual_affected_rows, "mysqltest write matched the result contract");
  return succeeded && affected;
}

/** Assert a one-cell integer query against its expected result transcript. */
bool read_single_integer(TestContext &context, ObISQLClient &client,
                         const char *sql, int64_t expected)
{
  ObISQLClient::ReadResult result;
  int status = client.read(result, sql);
  auto *rows = status == OB_SUCCESS ? result.get_result() : nullptr;
  int64_t actual = 0;
  if (status == OB_SUCCESS && rows == nullptr) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  if (status == OB_SUCCESS) {
    status = rows->next();
  }
  if (status == OB_SUCCESS && rows->get_column_count() != 1) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  if (status == OB_SUCCESS) {
    status = rows->get_int(0, actual);
  }
  if (status == OB_SUCCESS && rows->next() != OB_ITER_END) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  context.assert_equal("count.result", OB_SUCCESS, status,
                       "mysqltest query produced one readable result row");
  context.assert_equal("count.value", expected, actual,
                       "mysqltest query row matched the expected transcript");
  return status == OB_SUCCESS && actual == expected;
}

/** Assert an empty result while retaining the exact expected column count. */
bool read_empty(TestContext &context, ObISQLClient &client, const char *name,
                const char *sql, int64_t expected_columns)
{
  ObISQLClient::ReadResult result;
  int status = client.read(result, sql);
  auto *rows = status == OB_SUCCESS ? result.get_result() : nullptr;
  if (status == OB_SUCCESS && rows == nullptr) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  if (status == OB_SUCCESS && rows->get_column_count() != expected_columns) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  if (status == OB_SUCCESS && rows->next() != OB_ITER_END) {
    status = oceanbase::common::OB_ERR_UNEXPECTED;
  }
  return context.assert_equal(
      std::string(name) + ".transcript", OB_SUCCESS, status,
      "mysqltest query matched its empty-row result transcript");
}

/** Run the active empty_table mysqltest source through the internal SQL proxy. */
int run_empty_table(TestContext &context)
{
  oceanbase::lib::ObStackHeaderGuard stack_header;
  oceanbase::lib::Worker worker;
  oceanbase::lib::Worker::set_worker_to_thread_local(&worker);
  SERVER_MODULE_SCOPE {
    ObISQLClient &client = ObServer::get_instance().get_mysql_proxy();
    bool proceed = write_exact(context, client, "session_defaults",
                               "set @@session.explicit_defaults_for_timestamp=off",
                               expected_affected_rows[0]);
    proceed = proceed && write_exact(
        context, client, "drop_before", expected_transcript[0],
        expected_affected_rows[1]);
    proceed = proceed && write_exact(
        context, client, "create",
        expected_transcript[1],
        expected_affected_rows[2]);
    proceed = proceed && read_single_integer(
        context, client, expected_transcript[2], 0);
    proceed = proceed && read_empty(
        context, client, "all_rows", expected_transcript[5], 3);
    proceed = proceed && read_empty(
        context, client, "zero_limit", expected_transcript[7], 3);
    if (proceed) {
      write_exact(context, client, "drop_after", expected_transcript[9],
                  expected_affected_rows[3]);
    }
  }
  oceanbase::lib::Worker::set_worker_to_thread_local(nullptr);
  return context.failure_count();
}

} // namespace

DeviceTestRegistry make_mysqltest_device_registry()
{
  DeviceTestRegistry registry;
  registry.add({"ios.mysqltest.empty_table", "mysqltest", 60, run_empty_table});
  return registry;
}

} // namespace seekdb::ios_test
