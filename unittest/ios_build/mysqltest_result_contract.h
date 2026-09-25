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
