// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "device_test_registry.h"
#include "observer/ob_server.h"
#include "common/mysqlclient/ob_mysql_proxy.h"
#include "common/mysqlclient/ob_mysql_result.h"
#include "common/mysqlclient/ob_mysql_transaction.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "share/rc/ob_server_runtime.h"
#include <mach/mach.h>
#include <chrono>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

namespace seekdb::ios_test {
namespace {
using namespace oceanbase;
using namespace oceanbase::common;
const char *EXACT = "SELECT id FROM ios_extended.points ORDER BY l2_distance(v,[0.1,0,0]) LIMIT 2";
const char *ANN = "SELECT /*+ INDEX(points vec_idx) */ id FROM ios_extended.points ORDER BY l2_distance(v,[0.1,0,0]) APPROXIMATE LIMIT 2";

/** Read one integer column, retaining database errors without recording premature assertions. */
int integers(ObISQLClient &client, const char *sql, std::vector<int64_t> &values)
{
  ObISQLClient::ReadResult result;
  int status = client.read(result, sql);
  auto *rows = status == OB_SUCCESS ? result.get_result() : nullptr;
  if (status == OB_SUCCESS && rows == nullptr) status = OB_ERR_UNEXPECTED;
  while (status == OB_SUCCESS && (status = rows->next()) == OB_SUCCESS) {
    int64_t value = 0;
    if (rows->get_column_count() != 1) status = OB_ERR_UNEXPECTED;
    if (status == OB_SUCCESS) status = rows->get_int(static_cast<int64_t>(0), value);
    if (status == OB_SUCCESS) values.push_back(value);
  }
  return status == OB_ITER_END ? OB_SUCCESS : status;
}

/** Execute a write and record its SQL status and optional affected-row assertion. */
bool write(TestContext &context, ObISQLClient &client, const char *name, const char *sql,
           int64_t expected = -1)
{
  int64_t affected = 0;
  const int status = client.write(sql, affected);
  bool success = context.assert_equal(std::string(name) + ".status", OB_SUCCESS, status, sql);
  if (success && expected >= 0) {
    success = context.assert_equal(std::string(name) + ".affected", expected, affected, sql);
  }
  return success;
}

/** Compare deterministic result IDs, optionally waiting for asynchronous ANN visibility. */
bool read(TestContext &context, ObISQLClient &client, const char *name, const char *sql,
          const std::vector<int64_t> &expected, bool asynchronous = false)
{
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
  std::vector<int64_t> actual;
  int status;
  do {
    actual.clear();
    status = integers(client, sql, actual);
    if (status == OB_SUCCESS && actual == expected) break;
    if (!asynchronous) break;
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
  } while (std::chrono::steady_clock::now() < deadline);
  context.assert_equal(std::string(name) + ".status", OB_SUCCESS, status, sql);
  return context.assert_true(std::string(name) + ".rows", status == OB_SUCCESS && actual == expected,
                             "ordered IDs must match the deterministic five-vector corpus");
}

/** Require the ANN plan to execute the named vector index rather than an exact table scan. */
bool indexed_plan(TestContext &context, ObMySQLProxy &client)
{
  ObISQLClient::ReadResult result;
  const std::string sql = std::string("EXPLAIN ") + ANN;
  int status = client.read(result, sql.c_str());
  auto *rows = status == OB_SUCCESS ? result.get_result() : nullptr;
  if (status == OB_SUCCESS && rows == nullptr) status = OB_ERR_UNEXPECTED;
  std::string plan;
  while (status == OB_SUCCESS && (status = rows->next()) == OB_SUCCESS) {
    ObString line;
    status = rows->get_varchar(static_cast<int64_t>(0), line);
    if (status == OB_SUCCESS) plan.append(line.ptr(), line.length());
  }
  if (status == OB_ITER_END) status = OB_SUCCESS;
  context.assert_equal("ann.plan.status", OB_SUCCESS, status, "read the device optimizer plan");
  return context.assert_true("ann.plan.index", status == OB_SUCCESS &&
      plan.find("VECTOR INDEX") != std::string::npos && plan.find("vec_idx") != std::string::npos,
      "ANN must execute the named vec_idx vector index");
}

/** Create only test-owned fixtures and a small HNSW index with exact known neighbors. */
bool seed(TestContext &context, ObMySQLProxy &client)
{
  return write(context, client, "database", "CREATE DATABASE IF NOT EXISTS ios_extended") &&
      write(context, client, "drop", "DROP TABLE IF EXISTS ios_extended.points") &&
      write(context, client, "create", "CREATE TABLE ios_extended.points(id INT PRIMARY KEY, grp INT, v VECTOR(3), VECTOR INDEX vec_idx(v) WITH(distance=l2,lib=vsag,type=hnsw,m=8,ef_construction=40,ef_search=40))") &&
      write(context, client, "insert", "INSERT INTO ios_extended.points VALUES(1,0,'[0,0,0]'),(2,1,'[1,0,0]'),(3,0,'[0,3,0]'),(4,1,'[6,0,0]'),(5,0,'[12,0,0]')", 5) &&
      read(context, client, "readback", "SELECT id FROM ios_extended.points WHERE id=1 AND l2_distance(v,[0,0,0])=0", {1}) &&
      read(context, client, "exact", EXACT, {1,2}) &&
      indexed_plan(context, client) &&
      read(context, client, "ann", ANN, {1,2}, true) &&
      read(context, client, "filter", "SELECT id FROM ios_extended.points WHERE grp=1 ORDER BY l2_distance(v,[0.1,0,0]) APPROXIMATE LIMIT 2", {2,4}, true);
}

/** Verify persisted index results and committed/rolled-back mutations after a clean relaunch. */
int restore(TestContext &context, ObMySQLProxy &client)
{
  if (!read(context, client, "persisted.rows", "SELECT id FROM ios_extended.points ORDER BY id", {1,2,3,4,5}) ||
      !indexed_plan(context, client) ||
      !read(context, client, "persisted.ann", ANN, {1,2}, true)) return context.failure_count();
  ObMySQLTransaction transaction;
  int status = transaction.start(&client);
  context.assert_equal("transaction.begin", OB_SUCCESS, status, "start vector rollback transaction");
  if (status == OB_SUCCESS) {
    write(context, transaction, "transaction.insert", "INSERT INTO ios_extended.points VALUES(6,1,'[0.3,0,0]')", 1);
    read(context, transaction, "transaction.visibility", "SELECT COUNT(*) FROM ios_extended.points WHERE id=6", {1});
    context.assert_equal("transaction.rollback", OB_SUCCESS, transaction.end(false), "rollback vector insert");
    read(context, client, "rollback.visibility", "SELECT COUNT(*) FROM ios_extended.points WHERE id=6", {0});
  }
  status = transaction.start(&client);
  context.assert_equal("commit.begin", OB_SUCCESS, status, "start vector commit transaction");
  if (status == OB_SUCCESS) {
    write(context, transaction, "commit.insert", "INSERT INTO ios_extended.points VALUES(6,1,'[0.3,0,0]')", 1);
    context.assert_equal("transaction.commit", OB_SUCCESS, transaction.end(true), "commit vector insert");
    read(context, client, "commit.ann", ANN, {1,6}, true);
    write(context, client, "update", "UPDATE ios_extended.points SET v='[20,0,0]' WHERE id=6", 1);
    read(context, client, "update.ann", ANN, {1,2}, true);
    write(context, client, "delete", "DELETE FROM ios_extended.points WHERE id=6", 1);
    read(context, client, "delete.visibility", "SELECT COUNT(*) FROM ios_extended.points WHERE id=6", {0});
  }
  for (int index = 0; index < 32 && context.failure_count() == 0; ++index) {
    read(context, client, "repeat.ann", ANN, {1,2}, true);
  }
  return context.failure_count();
}

/** Record the process footprint reported by the device kernel, failing unavailable metrics. */
void footprint(TestContext &context, const char *name)
{
  task_vm_info_data_t info{};
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  const kern_return_t status = task_info(mach_task_self(), TASK_VM_INFO,
                                       reinterpret_cast<task_info_t>(&info), &count);
  context.assert_equal(name, KERN_SUCCESS, status, "device footprint_bytes=" + std::to_string(info.phys_footprint));
}

/** Touch bounded allocation levels and verify SQL/vector responsiveness after release. */
int memory(TestContext &context, ObMySQLProxy &client)
{
  footprint(context, "footprint.idle");
  if (!seed(context, client)) return context.failure_count();
  footprint(context, "footprint.indexed");
  for (const size_t bytes : {8UL*1024*1024, 32UL*1024*1024}) {
    {
      std::unique_ptr<unsigned char, decltype(&std::free)> allocation(
          static_cast<unsigned char *>(std::malloc(bytes)), &std::free);
      if (!context.assert_true("pressure.allocate", allocation != nullptr,
                               "bounded allocation_bytes=" + std::to_string(bytes))) break;
      for (size_t offset = 0; offset < bytes; offset += 4096) allocation.get()[offset] = 0x5a;
      size_t verified = 0;
      for (size_t offset = 0; offset < bytes; offset += 4096) verified += allocation.get()[offset] == 0x5a;
      context.assert_equal("pressure.pages", bytes/4096, verified, "every touched page retains its value");
      footprint(context, "footprint.pressure");
      read(context, client, "pressure.sql", "SELECT COUNT(*) FROM ios_extended.points", {5});
      read(context, client, "pressure.ann", ANN, {1,2}, true);
    }
    footprint(context, "footprint.released");
    read(context, client, "released.ann", ANN, {1,2}, true);
  }
  return context.failure_count();
}

/** Bind the engine worker and module lifetime for one isolated SQL-backed device case. */
template <typename Function>
int sql_case(TestContext &context, Function function)
{
  lib::ObStackHeaderGuard stack;
  lib::Worker worker;
  lib::Worker::set_worker_to_thread_local(&worker);
  if (context.assert_true("modules.ready", share::g_server_modules_ready, "engine modules are initialized")) {
    SERVER_MODULE_SCOPE { function(context, observer::ObServer::get_instance().get_mysql_proxy()); }
  }
  lib::Worker::set_worker_to_thread_local(nullptr);
  return context.failure_count();
}
/** Run the vector seed case with an initialized engine worker. */
int vector_seed(TestContext &context) { return sql_case(context, seed); }
/** Run the vector recovery case with an initialized engine worker. */
int vector_restore(TestContext &context) { return sql_case(context, restore); }
/** Run bounded memory pressure with an initialized engine worker. */
int memory_pressure(TestContext &context) { return sql_case(context, memory); }
} // namespace

/** Register deterministic vector and bounded-memory cases executed on the physical device. */
DeviceTestRegistry make_extended_device_registry()
{
  DeviceTestRegistry registry;
  registry.add({"ios.vector.seed", "vector", 180, vector_seed});
  registry.add({"ios.vector.restore", "vector", 180, vector_restore});
  registry.add({"ios.memory.bounded-pressure", "memory", 180, memory_pressure});
  return registry;
}
} // namespace seekdb::ios_test
