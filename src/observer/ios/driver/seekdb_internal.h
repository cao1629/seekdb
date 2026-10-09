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
#include <mysql.h>
/** Socket-only handle owned by the framework lifecycle adapter. */
typedef struct {
    char *db_dir;
    char *sock_path;
    char *socket_alias_dir;
    char host[64];
    int port;
} SeekdbHandleImpl;
typedef struct {
    MYSQL *mysql;
} SeekdbConnectionImpl;

typedef struct {
    SeekdbTypeId type;
    union {
        int64_t i64;
        uint64_t u64;
        double f64;
        struct {
            char *data;
            size_t len;
        } str;
    } v;
} SeekdbValueImpl;

typedef struct {
    int column_count;
    MYSQL *mysql; /* connection that produced this result;
                    used by seekdb_result_next to call
                    mysql_errno when fetch returns NULL */
    MYSQL_RES *mysql_res;
    MYSQL_ROW current_row;          /* set by seekdb_result_next */
    unsigned long *current_lengths; /* pointer into MYSQL_RES storage,
                                      overwritten on next fetch */
} SeekdbResultImpl;
