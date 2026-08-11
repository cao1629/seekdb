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

#[no_mangle]
extern "C" fn ob_sql_sock_handler_on_connect(
    _handler: *mut c_void,
    _sess: *mut c_void,
    _fd: c_int,
    _is_unix: c_int,
    _greeting: *mut NioGreetingInfo,
) -> c_int {
    0
}

#[no_mangle]
extern "C" fn ob_sql_sock_handler_on_readable(
    _handler: *mut c_void,
    _sess: *mut c_void,
    _body: *mut c_char,
    _body_len: i64,
    _wire_bytes: u64,
    _packet_kind: c_int,
    _command_view: *const NioMysqlCommandView,
    _generation: u64,
) -> c_int {
    0
}

#[no_mangle]
extern "C" fn ob_sql_sock_handler_on_disconnect(_handler: *mut c_void, _sess: *mut c_void) {}

#[no_mangle]
extern "C" fn ob_sql_sock_handler_on_close(
    _handler: *mut c_void,
    _sess: *mut c_void,
    _err: c_int,
) {
}
