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

#include <gtest/gtest.h>
#include <cstdio>

#include "lib/container/ob_vector.h"
#include "lib/allocator/page_arena.h"

using namespace oceanbase::common;

class TestObVector : public ::testing::Test
{
protected:
  TestObVector() {}
  virtual ~TestObVector() {}
  virtual void SetUp() {}
  virtual void TearDown() {}
};

TEST_F(TestObVector, test_basic_int)
{
  ObVector<int> vec;

  // push_back
  for (int i = 0; i < 10; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i));
  }

  ASSERT_EQ(10, vec.size());

  // access elements
  for (int i = 0; i < 10; ++i) {
    ASSERT_EQ(i, vec[i]);
  }

  printf("test_basic_int: size=%ld\n", vec.size());
}

TEST_F(TestObVector, test_pointer)
{
  ObVector<int *> vec;

  int a = 10, b = 20, c = 30;
  ASSERT_EQ(OB_SUCCESS, vec.push_back(&a));
  ASSERT_EQ(OB_SUCCESS, vec.push_back(&b));
  ASSERT_EQ(OB_SUCCESS, vec.push_back(&c));

  ASSERT_EQ(3, vec.size());
  ASSERT_EQ(10, *vec[0]);
  ASSERT_EQ(20, *vec[1]);
  ASSERT_EQ(30, *vec[2]);

  printf("test_pointer: size=%ld, values=%d,%d,%d\n",
         vec.size(), *vec[0], *vec[1], *vec[2]);
}

TEST_F(TestObVector, test_with_arena_allocator)
{
  ObArenaAllocator alloc;
  ObVector<int, ObArenaAllocator> vec(&alloc);

  for (int i = 0; i < 100; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i * 2));
  }

  ASSERT_EQ(100, vec.size());

  // verify values
  for (int i = 0; i < 100; ++i) {
    ASSERT_EQ(i * 2, vec[i]);
  }

  printf("test_with_arena_allocator: size=%ld\n", vec.size());
}

TEST_F(TestObVector, test_clear_and_reset)
{
  ObVector<int> vec;

  for (int i = 0; i < 10; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i));
  }
  ASSERT_EQ(10, vec.size());

  // clear - keeps capacity
  vec.clear();
  ASSERT_EQ(0, vec.size());

  // can still push after clear
  for (int i = 0; i < 5; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i + 100));
  }
  ASSERT_EQ(5, vec.size());
  ASSERT_EQ(100, vec[0]);

  // reset - releases memory
  vec.reset();
  ASSERT_EQ(0, vec.size());

  printf("test_clear_and_reset: passed\n");
}

TEST_F(TestObVector, test_at)
{
  ObVector<int> vec;

  for (int i = 0; i < 5; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i * 10));
  }

  // at() returns pointer
  int *p = vec.at(2);
  ASSERT_NE(nullptr, p);
  ASSERT_EQ(20, *p);

  // modify through pointer
  *p = 999;
  ASSERT_EQ(999, vec[2]);

  printf("test_at: vec[2]=%d\n", vec[2]);
}

TEST_F(TestObVector, test_remove)
{
  ObVector<int> vec;

  for (int i = 0; i < 5; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i));
  }
  // vec = [0, 1, 2, 3, 4]

  // remove element at index 2
  ASSERT_EQ(OB_SUCCESS, vec.remove(2));
  // vec = [0, 1, 3, 4]

  ASSERT_EQ(4, vec.size());
  ASSERT_EQ(0, vec[0]);
  ASSERT_EQ(1, vec[1]);
  ASSERT_EQ(3, vec[2]);
  ASSERT_EQ(4, vec[3]);

  printf("test_remove: size=%ld, values=%d,%d,%d,%d\n",
         vec.size(), vec[0], vec[1], vec[2], vec[3]);
}

TEST_F(TestObVector, test_iteration)
{
  ObVector<int> vec;

  for (int i = 0; i < 5; ++i) {
    ASSERT_EQ(OB_SUCCESS, vec.push_back(i));
  }

  // iterate using index
  int sum = 0;
  for (int64_t i = 0; i < vec.size(); ++i) {
    sum += vec[i];
  }
  ASSERT_EQ(10, sum);  // 0+1+2+3+4 = 10

  printf("test_iteration: sum=%d\n", sum);
}

TEST_F(TestObVector, test_pointer_with_arena)
{
  ObArenaAllocator alloc;
  ObVector<int *, ObArenaAllocator> vec(&alloc);

  // allocate ints from arena and store pointers
  for (int i = 0; i < 5; ++i) {
    int *p = static_cast<int *>(alloc.alloc(sizeof(int)));
    ASSERT_NE(nullptr, p);
    *p = i * 100;
    ASSERT_EQ(OB_SUCCESS, vec.push_back(p));
  }

  ASSERT_EQ(5, vec.size());

  for (int64_t i = 0; i < vec.size(); ++i) {
    ASSERT_EQ(i * 100, *vec[i]);
  }

  printf("test_pointer_with_arena: size=%ld\n", vec.size());
}

int main(int argc, char **argv)
{
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
