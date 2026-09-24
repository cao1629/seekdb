// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/** Run generic SQL fixtures and persist per-step evidence when a path is provided. */
int seekdb_ios_probe_sql(const char *report_path, int64_t *previous_runs);
#ifdef __cplusplus
}
#endif
