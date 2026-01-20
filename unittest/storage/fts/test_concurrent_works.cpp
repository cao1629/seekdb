/*
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// put top to use macro tricks
#include "mtlenv/mock_tenant_module_env.h"
// put top to use macro tricks

#include "lib/ob_errno.h"
#include "lib/utility/ob_macro_utils.h"
#include "share/ob_thread_pool.h"

#include <gtest/gtest.h>
#include <unistd.h>
#include <atomic>

#define USING_LOG_PREFIX STORAGE_FTS

namespace oceanbase
{
namespace storage
{

class TestConcurrentWorks : public ::testing::Test
{
protected:
  TestConcurrentWorks() {}
  virtual ~TestConcurrentWorks() {}

  static void SetUpTestCase()
  {
    LOG_INFO("SetUpTestCase");
    EXPECT_EQ(OB_SUCCESS, MockTenantModuleEnv::get_instance().init());
  }

  static void TearDownTestCase()
  {
    LOG_INFO("TearDownTestCase");
    MockTenantModuleEnv::get_instance().destroy();
  }

  virtual void SetUp() {}
  virtual void TearDown() {}
};

// Simple thread pool that increments a counter
class CounterThreadPool : public share::ObThreadPool
{
public:
  CounterThreadPool() : counter_(0), work_count_(0) {}

  void set_work_count(int64_t count) { work_count_ = count; }
  int64_t get_counter() const { return counter_.load(); }

  void run1() override
  {
    lib::set_thread_name("CounterThread");
    while (!has_set_stop()) {
      int64_t current = counter_.fetch_add(1);
      if (current >= work_count_ - 1) {
        break;
      }
      ::usleep(100);  // small delay to simulate work
    }
  }

private:
  std::atomic<int64_t> counter_;
  int64_t work_count_;
};

// Thread pool that performs concurrent dictionary lookups
class WorkerThreadPool : public share::ObThreadPool
{
public:
  WorkerThreadPool() : completed_tasks_(0), total_tasks_(0) {}

  void set_total_tasks(int64_t tasks) { total_tasks_ = tasks; }
  int64_t get_completed_tasks() const { return completed_tasks_.load(); }

  void run1() override
  {
    lib::set_thread_name("WorkerThread");
    int64_t local_count = 0;

    while (!has_set_stop()) {
      int64_t task_id = completed_tasks_.fetch_add(1);
      if (task_id >= total_tasks_) {
        break;
      }

      // Simulate some work
      volatile int sum = 0;
      for (int i = 0; i < 1000; i++) {
        sum += i;
      }
      (void)sum;

      local_count++;
    }

    LOG_INFO("Worker thread completed", K(local_count));
  }

private:
  std::atomic<int64_t> completed_tasks_;
  int64_t total_tasks_;
};

TEST_F(TestConcurrentWorks, test_basic_thread_pool)
{
  CounterThreadPool pool;

  const int64_t thread_count = 4;
  const int64_t work_count = 100;

  pool.set_work_count(work_count);
  pool.set_thread_count(thread_count);

  int ret = pool.start();
  ASSERT_EQ(OB_SUCCESS, ret);

  // Wait for threads to complete work
  int max_wait_ms = 5000;
  while (pool.get_counter() < work_count && max_wait_ms > 0) {
    ::usleep(10000);  // 10ms
    max_wait_ms -= 10;
  }

  pool.stop();
  pool.wait();

  int64_t final_count = pool.get_counter();
  LOG_INFO("Final counter value", K(final_count), K(work_count));

  ASSERT_GE(final_count, work_count);
}

TEST_F(TestConcurrentWorks, test_multiple_workers)
{
  WorkerThreadPool pool;

  const int64_t thread_count = 8;
  const int64_t total_tasks = 1000;

  pool.set_total_tasks(total_tasks);
  pool.set_thread_count(thread_count);

  int ret = pool.start();
  ASSERT_EQ(OB_SUCCESS, ret);

  // Wait for all tasks to complete
  int max_wait_ms = 10000;
  while (pool.get_completed_tasks() < total_tasks && max_wait_ms > 0) {
    ::usleep(10000);  // 10ms
    max_wait_ms -= 10;
  }

  pool.stop();
  pool.wait();

  int64_t completed = pool.get_completed_tasks();
  LOG_INFO("Completed tasks", K(completed), K(total_tasks));

  ASSERT_GE(completed, total_tasks);
}

TEST_F(TestConcurrentWorks, test_thread_count_change)
{
  WorkerThreadPool pool;

  const int64_t initial_threads = 2;
  const int64_t total_tasks = 500;

  pool.set_total_tasks(total_tasks);
  pool.set_thread_count(initial_threads);

  int ret = pool.start();
  ASSERT_EQ(OB_SUCCESS, ret);

  // Let some work happen
  ::usleep(50000);  // 50ms

  // Increase thread count
  ret = pool.set_thread_count(6);
  ASSERT_EQ(OB_SUCCESS, ret);

  // Wait for all tasks to complete
  int max_wait_ms = 10000;
  while (pool.get_completed_tasks() < total_tasks && max_wait_ms > 0) {
    ::usleep(10000);  // 10ms
    max_wait_ms -= 10;
  }

  pool.stop();
  pool.wait();

  int64_t completed = pool.get_completed_tasks();
  LOG_INFO("Completed tasks after thread increase", K(completed), K(total_tasks));

  ASSERT_GE(completed, total_tasks);
}

} // end namespace storage
} // end namespace oceanbase

int main(int argc, char **argv)
{
  system("rm -rf test_concurrent_works.log");
  OB_LOGGER.set_file_name("test_concurrent_works.log", true);
  OB_LOGGER.set_log_level("INFO");
  testing::InitGoogleTest(&argc, argv);

  return RUN_ALL_TESTS();
}
