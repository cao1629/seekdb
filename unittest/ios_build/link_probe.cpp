// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "seekdb_ios.h"
#include <cstdlib>

/** Force the runtime's dependency closure into a link-only iOS executable. */
int main()
{
  const char *directory = std::getenv("SEEKDB_IOS_TEST_DIRECTORY");
  return directory == nullptr ? 0 : seekdb_ios_run(directory);
}
