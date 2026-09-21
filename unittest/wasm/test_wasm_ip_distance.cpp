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

#include "data_plane/vector/ob_vector_ip_distance.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <cfloat>
#include <cmath>
#include <cstdio>

using namespace oceanbase::common;

int main()
{
  init_arches();
  const float first[] = {1, 2, 3, 4, 5, 6, 7, 8, 9};
  const float second[] = {2, 3, 4, 5, 6, 7, 8, 9, 10};
  for (const double initial : {DBL_MAX, 123.0, -19.0}) {
    for (const int64_t length : {0, 3, 9}) {
      double distance = initial;
      assert(ObVectorIpDistance<float>::ip_distance_func(first, second, length, distance) == OB_SUCCESS);
      const double expected = length == 0 ? 0 : length == 3 ? 20 : 330;
      assert(distance == expected);
    }
  }

  std::array<std::array<float, 3>, 5> vectors = {{
    {0, 1, 2}, {1, 1, 2}, {1, 2, 3}, {100, 1, 2}, {1000, 100, 1}
  }};
  for (auto &vector : vectors) {
    const double norm = std::sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]);
    for (auto &component : vector) {
      component /= norm;
    }
  }
  std::array<double, 5> distances;
  for (size_t i = 0; i < vectors.size(); ++i) {
    distances[i] = DBL_MAX;
    assert(ObVectorIpDistance<float>::ip_distance_func(vectors[1].data(), vectors[i].data(),
                                                      3, distances[i]) == OB_SUCCESS);
    assert(std::isfinite(distances[i]) && distances[i] <= 1.000001);
  }
  std::array<int, 5> order = {1, 2, 3, 4, 5};
  std::sort(order.begin(), order.end(), [&](int left, int right) {
    return distances[left - 1] > distances[right - 1];
  });
  assert((order == std::array<int, 5>{2, 3, 1, 5, 4}));
  std::puts("PASS: inner product replaces initial distance and sorts normalized vectors correctly");
}
