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
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/** Run generic SQL fixtures and persist per-step evidence when a path is provided. */
int seekdb_ios_probe_sql(const char *report_path, int64_t *previous_runs);
#ifdef __cplusplus
}
#endif
