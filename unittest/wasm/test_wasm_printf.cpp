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

#include "lib/utility/ob_printf.h"
#include <cassert>
#include <cstdint>
#include <limits>
#include <string>
#include <sys/time.h>

using oceanbase::common::ob_vsnprintf;

static int format(char *buf, size_t size, const char *fmt, ...)
{
  va_list args;
  va_start(args, fmt);
  const int result = ob_vsnprintf(buf, size, fmt, args);
  va_end(args);
  return result;
}

static void check_integer_widths()
{
  char buf[512];
  const int64_t minimum = std::numeric_limits<int64_t>::min();
  const uint64_t maximum = std::numeric_limits<uint64_t>::max();
  const int result = format(buf, sizeof(buf), "%ld/%lu/%lld/%llu/%d/%u/%.*s",
      minimum, maximum, -7LL, 8ULL, -9, 10U, 4, "tail-left");
  const char *expected = "-9223372036854775808/18446744073709551615/-7/8/-9/10/tail";
  assert(0 == strcmp(buf, expected));
  assert(result == static_cast<int>(strlen(expected)));
  assert(format(buf, sizeof(buf), "%ld/%lu", static_cast<int64_t>(-2147483647L),
      static_cast<uint64_t>(4294967295UL)) == 22);
  assert(0 == strcmp(buf, "-2147483647/4294967295"));
}

static void check_format_specifiers()
{
  char buf[512];
  format(buf, sizeof(buf), "%%ld|%+08ld|%#lx|%lo|%lX|%*.*ld|%.*s|%.2lf|%zu|%td",
      int64_t(42), uint64_t(42), uint64_t(42), uint64_t(42), 8, 4, int64_t(42),
      3, "abcdef", 2.5, size_t(123), ptrdiff_t(-12));
  assert(0 == strcmp(buf, "%ld|+0000042|0x2a|52|2A|    0042|abc|2.50|123|-12"));
  format(buf, sizeof(buf), "%2$ld/%1$s/%3$.*4$s", "first", int64_t(42), "last", 2);
  assert(0 == strcmp(buf, "42/first/la"));
  int written = -1;
  format(buf, sizeof(buf), "%ld%n/%s", int64_t(123), &written, "last");
  assert(3 == written);
  assert(0 == strcmp(buf, "123/last"));
  char pointer[64];
  snprintf(pointer, sizeof(pointer), "%p", static_cast<void *>(buf));
  format(buf, sizeof(buf), "%ld/%p/%s", int64_t(42), static_cast<void *>(buf), "last");
  assert(std::string(buf) == std::string("42/") + pointer + "/last");
}

static void check_lengths()
{
  const std::string prefix(8192, 'x');
  const std::string fmt = prefix + "%ld/%s";
  const std::string expected = prefix + "9223372036854775807/tail";
  std::string buf(expected.size() + 1, '\0');
  const int64_t maximum = std::numeric_limits<int64_t>::max();
  assert(format(nullptr, 0, fmt.c_str(), maximum, "tail") == expected.size());
  assert(format(&buf[0], buf.size(), fmt.c_str(), maximum, "tail") == expected.size());
  assert(0 == strcmp(buf.c_str(), expected.c_str()));
  char small[5];
  assert(8 == format(small, sizeof(small), "%ld/%s", int64_t(123), "tail"));
  assert(0 == strcmp(small, "123/"));
  assert(3 == format(small, sizeof(small), "%%ld"));
  assert(0 == strcmp(small, "%ld"));
}

static void check_timeval_log_header()
{
  char buf[512];
  timeval tv{};
  tv.tv_sec = 1792217700;
  tv.tv_usec = 123456;
  format(buf, sizeof(buf), "[%04d-%02d-%02d %02d:%02d:%02d.%06ld] "
      "%-5s %s%s (%s:%d) [%ld][%s][%s] [lt=%ld]%s ",
      2026, 10, 17, 10, 15, 0, static_cast<int64_t>(tv.tv_usec),
      "WARN", "[LIB]", "log_head", "ob_log.cpp", 1, int64_t(123), "worker",
      "trace", int64_t(0), "[errcode=0]");
  assert(0 == strcmp(buf, "[2026-10-17 10:15:00.123456] WARN  [LIB]log_head "
      "(ob_log.cpp:1) [123][worker][trace] [lt=0][errcode=0] "));
  format(buf, sizeof(buf), "%ld/%ld/%s", static_cast<int64_t>(tv.tv_sec),
      static_cast<int64_t>(tv.tv_usec), "tail");
  assert(0 == strcmp(buf, "1792217700/123456/tail"));
}

int main()
{
  check_integer_widths();
  check_format_specifiers();
  check_lengths();
  check_timeval_log_header();
  puts("printf portability checks passed");
}
