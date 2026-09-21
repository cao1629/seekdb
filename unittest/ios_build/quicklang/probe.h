// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
/** Run QuickLang SQL fixtures and flush per-step JSONL evidence to an absolute sandbox path. */
int seekdb_ios_probe_quicklang(const char *report_path);
#ifdef __cplusplus
}
#endif
