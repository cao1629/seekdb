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
#include "seekdb.h"
#include "seekdb_ios.h"

/** Load every desktop entry point independently from the embedded framework. */
struct Driver {
#define ENTRY(name) decltype(&name) name = nullptr;
  ENTRY(seekdb_open) ENTRY(seekdb_close) ENTRY(seekdb_connection_options)
  ENTRY(seekdb_connect) ENTRY(seekdb_disconnect) ENTRY(seekdb_last_error)
  ENTRY(seekdb_query) ENTRY(seekdb_result_free) ENTRY(seekdb_result_column_count)
  ENTRY(seekdb_result_column_name) ENTRY(seekdb_result_column_type_id)
  ENTRY(seekdb_result_row_count) ENTRY(seekdb_result_next)
  ENTRY(seekdb_result_get_int64) ENTRY(seekdb_result_get_uint64)
  ENTRY(seekdb_result_get_float) ENTRY(seekdb_result_get_str)
  ENTRY(seekdb_trx_begin) ENTRY(seekdb_trx_commit) ENTRY(seekdb_trx_rollback)
  ENTRY(seekdb_value_free) ENTRY(seekdb_value_create_int64) ENTRY(seekdb_value_get_int64)
  ENTRY(seekdb_malloc) ENTRY(seekdb_free)
  ENTRY(seekdb_ios_get_state) ENTRY(seekdb_ios_get_cleanup_status)
  ENTRY(seekdb_ios_get_cleanup_error) ENTRY(seekdb_ios_get_build_id) ENTRY(seekdb_ios_get_hook_mode)
#undef ENTRY
};

