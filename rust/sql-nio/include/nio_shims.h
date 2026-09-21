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

#ifndef NIO_SHIMS_H
#define NIO_SHIMS_H

#include <stdint.h>

#include "nio.h"

#ifdef __cplusplus
extern "C" {
#endif

int ob_sql_sock_handler_on_connect(void *handler, void *sess, int fd,
                                   int is_unix, NioGreetingInfo *greeting);
int ob_sql_sock_handler_on_readable(void *handler, void *sess, char *body,
                                    int64_t body_len, uint64_t wire_bytes,
                                    int packet_kind,
                                    const NioMysqlCommandView *command_view,
                                    uint64_t generation);
void ob_sql_sock_handler_on_disconnect(void *handler, void *sess);
void ob_sql_sock_handler_on_close(void *handler, void *sess, int err);

#ifdef __cplusplus
}
#endif

#endif
