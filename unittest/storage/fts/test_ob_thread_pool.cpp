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
#include <cstdio>

#define USING_LOG_PREFIX STORAGE_FTS

namespace oceanbase
{
namespace storage
{

class TestObThreadPool : public ::testing::Test
{
protected:
  TestObThreadPool() {}
  virtual ~TestObThreadPool() {}

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

// Simplest thread pool example
class SimplestThreadPool : public share::ObThreadPool
{
  void run1() override
  {
    int64_t idx = get_thread_idx();
    for (int i = 0; i < 10; i++) {
      printf("Thread %ld, i = %d\n", idx, i);
    }
  }
};

TEST_F(TestObThreadPool, test_simplest_thread_pool)
{
  SimplestThreadPool pool;
  pool.set_thread_count(3);
  pool.start();
  pool.wait();
}

// Thread pool that computes sum of a range concurrently
class SumThreadPool : public share::ObThreadPool
{
public:
  SumThreadPool() : total_sum_(0), n_(0) {}

  void set_n(int64_t n) { n_ = n; }
  int64_t get_sum() const { return total_sum_.load(); }

  void run1() override
  {
    lib::set_thread_name("SumThread");
    int64_t thread_count = get_thread_count();
    int64_t idx = get_thread_idx();

    // Each thread computes sum for its portion: [start, end)
    int64_t chunk_size = (n_ + thread_count - 1) / thread_count;
    int64_t start = idx * chunk_size + 1;
    int64_t end = std::min((idx + 1) * chunk_size + 1, n_ + 1);

    int64_t local_sum = 0;
    for (int64_t i = start; i < end; ++i) {
      local_sum += i;
    }

    // Add to total
    total_sum_.fetch_add(local_sum);

    printf("Thread %ld computed partial sum: start=%ld, end=%ld, local_sum=%ld\n",
           idx, start, end, local_sum);
  }

private:
  std::atomic<int64_t> total_sum_;
  int64_t n_;
};

TEST_F(TestObThreadPool, test_concurrent_sum)
{
  SumThreadPool pool;

  const int64_t thread_count = 4;
  const int64_t n = 1000000;

  pool.set_n(n);
  pool.set_thread_count(thread_count);

  int ret = pool.start();
  ASSERT_EQ(OB_SUCCESS, ret);

  // Wait for threads to finish
  pool.wait();

  int64_t computed_sum = pool.get_sum();
  int64_t expected_sum = n * (n + 1) / 2;  // Formula: 1+2+...+n = n*(n+1)/2

  printf("Sum result: computed=%ld, expected=%ld, n=%ld\n", computed_sum, expected_sum, n);

  ASSERT_EQ(computed_sum, expected_sum);
}

} // end namespace storage
} // end namespace oceanbase

int main(int argc, char **argv)
{
  system("rm -rf test_ob_thread_pool.log");
  OB_LOGGER.set_file_name("test_ob_thread_pool.log", true);
  OB_LOGGER.set_log_level("INFO");
  testing::InitGoogleTest(&argc, argv);

  return RUN_ALL_TESTS();
}
