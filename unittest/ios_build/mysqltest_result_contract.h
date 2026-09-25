#pragma once

#include <array>
#include <string_view>

namespace seekdb::ios_test {

/** Validate ordered empty-result field labels and the terminal iterator status. */
template <std::size_t N>
bool empty_result_matches(
    const std::array<std::string_view, N> &actual_labels,
    const std::array<std::string_view, N> &expected_labels,
    int iterator_status, int expected_iterator_end)
{
  return actual_labels == expected_labels &&
         iterator_status == expected_iterator_end;
}

} // namespace seekdb::ios_test
