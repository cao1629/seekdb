// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "seekdb_ios.h"
#include "sql_probe.h"
#include <cstdlib>

/** Force the runtime's dependency closure into a link-only iOS executable. */
int main()
{
  const char *directory = std::getenv("SEEKDB_IOS_TEST_DIRECTORY");
  if (std::getenv("SEEKDB_IOS_TEST_SQL") != nullptr) {
    int64_t previous_runs = 0;
    return seekdb_ios_probe_sql(&previous_runs);
  }
  return directory == nullptr ? 0 : seekdb_ios_run(directory);
}
