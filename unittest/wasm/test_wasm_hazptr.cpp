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

#include "share/cache/ob_kvcache_hazard_domain.h"
#include "share/cache/ob_kvcache_struct.h"
#include <cassert>
#include <cstdio>

using namespace oceanbase::common;

int main()
{
  ObKVMemBlockHandle block;
  block.status_ = ObKVMBHandleStatus::USING;
  HazptrHolder sources[64];
  for (auto &source : sources) {
    bool success = false;
    assert(source.protect(success, &block) == OB_SUCCESS);
    assert(success && source.get_mb_handle() == &block);
  }
  block.status_ = ObKVMBHandleStatus::FREE;
  for (auto &source : sources) {
    HazptrHolder second;
    HazptrHolder third;
    assert(second.assign(source) == OB_SUCCESS);
    assert(source.get_mb_handle() == &block);
    assert(second.get_mb_handle() == &block);
    assert(third.assign(second) == OB_SUCCESS);
    source.reset();
    assert(second.get_mb_handle() == &block);
    assert(third.get_mb_handle() == &block);
    second.reset();
    assert(third.get_mb_handle() == &block);
    third.reset();
    assert(!source.is_valid() && !second.is_valid() && !third.is_valid());
  }
  std::puts("PASS: retired cache block protection survives shared hazard pointer copies and resets");
}
