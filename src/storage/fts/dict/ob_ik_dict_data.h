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

#ifndef _OCEANBASE_STORAGE_FTS_DICT_OB_IK_DICT_DATA_H_
#define _OCEANBASE_STORAGE_FTS_DICT_OB_IK_DICT_DATA_H_

namespace oceanbase
{
namespace storage
{

// IK main dictionary data (embedded via objcopy)
// Use: data pointer = ik_main_dat_bg, size = ik_main_dat_ed - ik_main_dat_bg
extern const char ik_main_dat_bg[];
extern const char ik_main_dat_ed[];

// IK quantifier dictionary data (embedded via objcopy)
extern const char ik_quan_dat_bg[];
extern const char ik_quan_dat_ed[];

// IK stop word dictionary data (embedded via objcopy)
extern const char ik_stop_dat_bg[];
extern const char ik_stop_dat_ed[];

} //  namespace storage
} //  namespace oceanbase

#endif // _OCEANBASE_STORAGE_FTS_DICT_OB_IK_DICT_DATA_H_
