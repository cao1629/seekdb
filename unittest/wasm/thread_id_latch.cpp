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
#include "lib/oblog/ob_log.h"
#undef LIB_LOG
#define LIB_LOG(...) ((void)0)
#undef COMMON_LOG
#define COMMON_LOG(...) ((void)0)
#include "lib/lock/ob_latch.h"
#include "lib/rc/context.h"
#include "lib/lock/ob_latch.cpp"
#include "lib/time/ob_time_utility.cpp"

int64_t thread_id_from_peer()
{
  return ob_gettid();
}
