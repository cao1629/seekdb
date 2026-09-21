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

#include "lib/charset/ob_ctype.h"
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>

struct IntegerParseCase
{
  const char *text;
  bool is_unsigned;
  uint64_t value;
  int error;
};

int main()
{
  const IntegerParseCase cases[] = {
    {"0", false, 0, 0},
    {"-1", false, UINT64_MAX, 0},
    {"9223372036854775807", false, INT64_MAX, 0},
    {"9223372036854775808", false, INT64_MAX, ERANGE},
    {"-9223372036854775808", false, uint64_t(INT64_MIN), 0},
    {"-9223372036854775809", false, uint64_t(INT64_MIN), ERANGE},
    {"-9223372036854775909", false, uint64_t(INT64_MIN), ERANGE},
    {"-9223372036854776909", false, uint64_t(INT64_MIN), ERANGE},
    {"9223372036854775807.5", false, INT64_MAX, ERANGE},
    {"-9223372036854775808.5", false, uint64_t(INT64_MIN), ERANGE},
    {"18446744073709551615", true, UINT64_MAX, 0},
    {"18446744073709551616", true, UINT64_MAX, ERANGE},
    {"-1", true, 0, ERANGE},
    {"-0", true, 0, 0},
    {"", false, 0, EDOM},
    {"not a number", false, 0, EDOM},
  };
  for (const auto &test : cases) {
    char *end = nullptr;
    int error = 0;
    const auto value = ob_strntoull10rnd_8bit(
        nullptr, test.text, std::strlen(test.text), test.is_unsigned, &end, &error);
    assert(value == test.value);
    assert(error == test.error);
    if (error != EDOM) {
      assert(end == test.text + std::strlen(test.text));
    }
  }
  std::puts("PASS: charset integer limits, rounding and platform errno values");
}
