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

#include "sql/optimizer/stat/ob_opt_column_stat.h"
#include "sql/optimizer/stat/ob_opt_table_stat.h"
#include "sql/optimizer/stat/ob_opt_ds_stat.h"
#include "sql/optimizer/stat/ob_opt_system_stat.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <new>

using namespace oceanbase::common;

template <typename Key, typename Init>
void check_key(Init init)
{
  alignas(Key) char first_storage[sizeof(Key)];
  alignas(Key) char second_storage[sizeof(Key)];
  alignas(Key) char copy_storage[sizeof(Key)];
  std::memset(first_storage, 0xa5, sizeof(first_storage));
  std::memset(second_storage, 0x5a, sizeof(second_storage));
  std::memset(copy_storage, 0x3c, sizeof(copy_storage));
  Key *first = new (first_storage) Key();
  Key *second = new (second_storage) Key();
  init(*first);
  init(*second);
  assert(first->operator==(*second));
  assert(first->hash() == second->hash());
  ObIKVCacheKey *copy = nullptr;
  assert(first->deep_copy(copy_storage, sizeof(copy_storage), copy) == OB_SUCCESS);
  assert(copy != nullptr && first->operator==(*copy));
  assert(first->hash() == copy->hash());
  copy->~ObIKVCacheKey();
  second->~Key();
  first->~Key();
}

int main()
{
  check_key<ObOptColumnStat::Key>([](auto &key) {
    key.table_id_ = 200001;
    key.partition_id_ = -1;
    key.column_id_ = 16;
  });
  check_key<ObOptTableStat::Key>([](auto &key) {
    key.table_id_ = 200001;
    key.partition_id_ = -1;
    key.tablet_id_ = 200002;
  });
  check_key<ObOptDSStat::Key>([](auto &key) {
    key.table_id_ = 200001;
    key.partition_hash_ = 123;
    key.ds_level_ = 1;
    key.sample_block_ = 8;
    key.expression_hash_ = 456;
  });
  check_key<ObOptSystemStat::Key>([](auto &) {});
  std::puts("PASS: equal optimizer statistics keys have equal hashes across padding and deep copies");
}
