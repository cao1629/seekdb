// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/** Verify SQL and increment a persistent counter; write the prior count and return zero or an engine error. */
int seekdb_ios_probe_sql(int64_t *previous_runs);
#ifdef __cplusplus
}
#endif
