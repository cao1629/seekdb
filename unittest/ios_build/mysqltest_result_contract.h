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

#pragma once

#include <array>
#include <string_view>

namespace seekdb::ios_test {

/** Validate ordered empty-result field labels and the terminal iterator status. */
template <typename Rows, std::size_t N>
bool empty_result_matches(
    const std::array<std::string_view, N> &actual_labels,
    const std::array<std::string_view, N> &expected_labels,
    Rows *rows, int expected_iterator_end)
{
  return rows != nullptr && actual_labels == expected_labels &&
         rows->next() == expected_iterator_end;
}

} // namespace seekdb::ios_test
