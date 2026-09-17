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

#include "share/schema/ob_table_schema.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <set>
#include <string>

using namespace oceanbase::common;
using namespace oceanbase::share::schema;

struct ConstraintNameCase
{
  ObConstraintType type;
  const char *marker;
};

static std::string recycle_name(ObConstraintType type,
                                uint64_t table_id,
                                uint64_t constraint_id)
{
  ObArenaAllocator allocator;
  ObString name;
  assert(ObTableSchema::create_cons_name_for_recyclebin(
      name, allocator, type, table_id, constraint_id) == OB_SUCCESS);
  assert(name.length() <= OB_MAX_CONSTRAINT_NAME_LENGTH_MYSQL);
  int64_t valid_bytes = 0;
  assert(ObCharset::well_formed_len(CS_TYPE_UTF8MB4_BIN,
      name.ptr(), name.length(), valid_bytes) == OB_SUCCESS);
  assert(valid_bytes == name.length());
  for (int32_t i = 0; i < name.length(); ++i) {
    assert(static_cast<unsigned char>(name.ptr()[i]) < 0x80);
  }
  return std::string(name.ptr(), name.length());
}

static void check_ordinary_name(const ConstraintNameCase &test)
{
  ObArenaAllocator allocator;
  ObString name;
  const int64_t before = ObTimeUtility::current_time();
  assert(ObTableSchema::create_cons_name_automatically(name,
      ObString::make_string("ordinary"), allocator, test.type) == OB_SUCCESS);
  const int64_t after = ObTimeUtility::current_time();
  const std::string value(name.ptr(), name.length());
  const std::string prefix = std::string("ordinary") + test.marker;
  assert(value.compare(0, prefix.size(), prefix) == 0);
  char *end = nullptr;
  const long long timestamp = std::strtoll(value.c_str() + prefix.size(), &end, 10);
  assert(end != value.c_str() + prefix.size() && *end == '\0');
  assert(timestamp >= before && timestamp <= after);
}

int main()
{
  assert(ObCharset::init_charset() == OB_SUCCESS);
  const ConstraintNameCase cases[] = {
    {CONSTRAINT_TYPE_PRIMARY_KEY, "_OBPK_"},
    {CONSTRAINT_TYPE_CHECK, "_OBCHECK_"},
    {CONSTRAINT_TYPE_UNIQUE_KEY, "_OBUNIQUE_"},
    {CONSTRAINT_TYPE_NOT_NULL, "_OBNOTNULL_"},
  };
  assert(recycle_name(CONSTRAINT_TYPE_CHECK, 500015, 500011)
      == "__recycle_$_C500015_OBCHECK_500011");
  assert(recycle_name(CONSTRAINT_TYPE_CHECK, 500015, 500012)
      == "__recycle_$_C500015_OBCHECK_500012");

  std::set<std::string> names;
  for (const auto &test : cases) {
    for (uint64_t id = 1; id <= 256; ++id) {
      assert(names.insert(recycle_name(test.type, 500015, id)).second);
    }
    assert(recycle_name(test.type, 500015, 1)
        != recycle_name(test.type, 500016, 1));
    assert(recycle_name(test.type, 12, 345)
        != recycle_name(test.type, 123, 45));
    assert(recycle_name(test.type, 1, 1)
        != recycle_name(test.type, (UINT64_C(1) << 32) + 1, 1));
    assert(recycle_name(test.type, 1, 1)
        != recycle_name(test.type, 1, (UINT64_C(1) << 32) + 1));
    const std::string first = recycle_name(test.type, 500015, 500011);
    assert(first == recycle_name(test.type, 500015, 500011));

    const std::string maximum = std::string("__recycle_$_C18446744073709551615")
        + test.marker + "18446744073709551615";
    assert(recycle_name(test.type, UINT64_MAX, UINT64_MAX) == maximum);
    assert(recycle_name(test.type, UINT64_MAX, UINT64_MAX)
        != recycle_name(test.type, UINT64_MAX - 1, UINT64_MAX));
    assert(recycle_name(test.type, UINT64_MAX, UINT64_MAX)
        != recycle_name(test.type, UINT64_MAX, UINT64_MAX - 1));

    ObTableSchema table;
    table.set_table_id(500015);
    const std::string characters[] = {"\xc3\xa9", "\xe4\xb8\xad", "\xf0\x9f\x98\x80"};
    for (const auto &character : characters) {
      for (size_t offset = 0; offset < character.size(); ++offset) {
        std::string table_name(offset, 'a');
        for (int i = 0; i < 32; ++i) { table_name += character; }
        assert(table.set_table_name(ObString(static_cast<int32_t>(table_name.size()),
            table_name.data())) == OB_SUCCESS);
        assert(recycle_name(test.type, table.get_table_id(), 500011) == first);
      }
    }
    check_ordinary_name(test);
  }
  assert(recycle_name(CONSTRAINT_TYPE_NOT_NULL, UINT64_MAX, UINT64_MAX).size() == 64);
  ObArenaAllocator allocator;
  ObString invalid;
  assert(ObTableSchema::create_cons_name_for_recyclebin(invalid,
      allocator, CONSTRAINT_TYPE_INVALID, 1, 1)
      == OB_ERR_UNEXPECTED);
  std::puts("PASS: recyclebin constraint names preserve unique IDs, byte bounds and UTF-8; ordinary names retain timestamps");
}
