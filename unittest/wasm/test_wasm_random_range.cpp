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

#include "lib/random/ob_random.h"
#include <array>
#include <cassert>
#include <cstdio>

using namespace oceanbase::common;

int main()
{
  ObRandom random;
  const std::array<int64_t, 4> sizes = {12, 15, 20, 25};
  const int64_t expected[4][8] = {
    {11, 3, 11, 7, 10, 7, 10, 9},
    {5, 6, 7, 13, 7, 12, 14, 12},
    {7, 7, 19, 18, 18, 11, 14, 14},
    {14, 18, 22, 13, 22, 12, 22, 20}
  };
  for (size_t row = 0; row < sizes.size(); ++row) {
    random.seed(1);
    for (int64_t i = 2; i < 10; ++i) {
      assert(random.get(i, sizes[row] - 1) == expected[row][i - 2]);
    }
    random.seed(1);
    for (int64_t i = 2; i < 10; ++i) {
      assert(random.get(sizes[row] - 1, i) == expected[row][i - 2]);
    }
  }

  random.seed(1);
  assert(random.get() == INT64_C(-1151252339));
  assert(random.get() == INT64_C(-2359585654202701071));
  assert(random.get() == INT64_C(-3794406216345016143));
  assert(random.get(INT64_MIN, INT64_MIN) == INT64_MIN);
  assert(random.get(INT64_MAX, INT64_MAX) == INT64_MAX);
  random.seed(1);
  assert(random.get(INT64_MIN, INT64_MAX) == INT64_MIN + INT64_C(1151252339));
  random.seed(1);
  assert(random.get(INT64_MAX, INT64_MIN) == INT64_MIN + INT64_C(1151252339));
  for (int i = 0; i < 20; ++i) {
    const int64_t low = INT64_MIN + 1;
    const int64_t high = INT64_MAX - 1;
    const int64_t instance_value = random.get(low, high);
    const int64_t thread_value = ObRandom::rand(low, high);
    assert(low <= instance_value && instance_value <= high);
    assert(low <= thread_value && thread_value <= high);
    assert(ObRandom::rand(INT64_MIN, INT64_MIN) == INT64_MIN);
    assert(ObRandom::rand(INT64_MAX, INT64_MAX) == INT64_MAX);
  }
  std::puts("PASS: random ranges preserve the native 64-bit seed sequence and integer boundaries");
}
