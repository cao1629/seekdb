// Copyright (c) 2025 OceanBase.
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

use crate::*;

extern "C" {
    pub fn ob_sql_sock_handler_on_connect(
        handler: *mut c_void,
        sess: *mut c_void,
        fd: c_int,
        is_unix: c_int,
        greeting: *mut NioGreetingInfo,
    ) -> c_int;
    pub fn ob_sql_sock_handler_on_readable(
        handler: *mut c_void,
        sess: *mut c_void,
        body: *mut c_char,
        body_len: i64,
        wire_bytes: u64,
        packet_kind: c_int,
        command_view: *const NioMysqlCommandView,
        generation: u64,
    ) -> c_int;
    pub fn ob_sql_sock_handler_on_disconnect(handler: *mut c_void, sess: *mut c_void);
    pub fn ob_sql_sock_handler_on_close(handler: *mut c_void, sess: *mut c_void, err: c_int);
}
