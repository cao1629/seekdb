// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "sql_probe.h"
#include "seekdb_ios.h"
#include "observer/ob_server.h"
#include "common/mysqlclient/ob_mysql_proxy.h"
#include "common/mysqlclient/ob_mysql_result.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "share/rc/ob_server_runtime.h"

using namespace oceanbase;
using namespace oceanbase::common;

namespace {
/** Read one integer from the internal SQL proxy and reject unexpected row counts. */
int read_scalar(ObMySQLProxy &proxy, const char *sql, int64_t &value)
{
  ObISQLClient::ReadResult result;
  int ret = proxy.read(result, sql);
  if (ret == OB_SUCCESS) {
    auto *rows = result.get_result();
    if (rows == nullptr) {
      ret = OB_ERR_UNEXPECTED;
    } else if ((ret = rows->next()) == OB_SUCCESS && (ret = rows->get_int("v", value)) == OB_SUCCESS) {
      ret = rows->next() == OB_ITER_END ? OB_SUCCESS : OB_ERR_UNEXPECTED;
    }
  }
  return ret;
}

/** Verify expression evaluation, DDL, mutation and readback in a probe-owned database. */
int verify_sql(int64_t &previous_runs)
{
  auto &proxy = observer::ObServer::get_instance().get_mysql_proxy();
  int64_t value = 0;
  int ret = read_scalar(proxy, "SELECT 6 * 7 AS v", value);
  if (ret == OB_SUCCESS && value != 42) {
    ret = OB_ERR_UNEXPECTED;
  }
  int64_t affected = 0;
  for (const char *sql : {
      "CREATE DATABASE IF NOT EXISTS ios_probe",
      "CREATE TABLE IF NOT EXISTS ios_probe.lifecycle (id INT PRIMARY KEY, runs BIGINT NOT NULL)"}) {
    if (ret == OB_SUCCESS) {
      ret = proxy.write(sql, affected);
    }
  }
  if (ret == OB_SUCCESS) {
    ret = read_scalar(proxy, "SELECT COALESCE(MAX(runs), 0) AS v FROM ios_probe.lifecycle WHERE id=1", previous_runs);
  }
  if (ret == OB_SUCCESS) {
    ret = proxy.write("INSERT INTO ios_probe.lifecycle VALUES (1,1) ON DUPLICATE KEY UPDATE runs=runs+1", affected);
  }
  if (ret == OB_SUCCESS) {
    ret = read_scalar(proxy, "SELECT runs AS v FROM ios_probe.lifecycle WHERE id=1", value);
    if (ret == OB_SUCCESS && value != previous_runs + 1) {
      ret = OB_ERR_UNEXPECTED;
    }
  }
  return ret;
}
}

/** Establish the engine thread context and run the test only on a running instance. */
int seekdb_ios_probe_sql(int64_t *previous_runs)
{
  if (previous_runs == nullptr || seekdb_ios_get_state() != SEEKDB_IOS_RUNNING) {
    return OB_INVALID_ARGUMENT;
  }
  lib::ObStackHeaderGuard stack_header;
  lib::Worker worker;
  lib::Worker::set_worker_to_thread_local(&worker);
  int ret = OB_ERR_UNEXPECTED;
  SERVER_MODULE_SCOPE {
    ret = verify_sql(*previous_runs);
  }
  lib::Worker::set_worker_to_thread_local(nullptr);
  return ret;
}
