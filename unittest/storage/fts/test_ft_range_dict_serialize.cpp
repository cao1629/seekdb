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

// put top to use macro tricks
#include "mtlenv/mock_tenant_module_env.h"
// put top to use macro tricks

#include "lib/allocator/page_arena.h"
#include "lib/ob_errno.h"
#include "share/ob_tenant_mem_limit_getter.h"
#include "storage/fts/dict/ob_ft_cache.h"
#include "storage/fts/dict/ob_ft_dict_def.h"
#include "storage/fts/dict/ob_ft_range_dict.h"
#include "storage/fts/dict/ob_ik_dic.h"

#include <gtest/gtest.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#define USING_LOG_PREFIX STORAGE_FTS

namespace oceanbase
{
namespace storage
{

class ObFTRangeDictSerializeTest : public ::testing::Test
{
protected:
  ObFTRangeDictSerializeTest() {}
  virtual ~ObFTRangeDictSerializeTest() {}

  static void SetUpTestCase()
  {
    LOG_INFO("SetUpTestCase");
    EXPECT_EQ(OB_SUCCESS, MockTenantModuleEnv::get_instance().init());
  }

  static void TearDownTestCase()
  {
    LOG_INFO("TearDownTestCase");
    MockTenantModuleEnv::get_instance().destroy();
  }

  virtual void SetUp()
  {
    // Create temporary directory
    test_dir_ = "/tmp/obft_dat_test";
    mkdir(test_dir_.c_str(), 0755);
  }

  virtual void TearDown()
  {
    // Cleanup temp directory
    if (!test_dir_.empty()) {
      std::string cmd = "rm -rf " + test_dir_;
      system(cmd.c_str());
    }
  }

  std::string test_dir_;
};


TEST_F(ObFTRangeDictSerializeTest, test_serialize_all_dicts)
{
  int ret = OB_SUCCESS;

  // Test serializing all 3 dictionaries (one file per dictionary, no range division)
  struct DictInfo {
    const char *name;
    ObFTDictType type;
    ObIKDictLoader::RawDict (*loader)();
  };

  DictInfo dicts[] = {
    {"main", ObFTDictType::DICT_IK_MAIN, ObIKDictLoader::dict_text},
    {"quan", ObFTDictType::DICT_IK_QUAN, ObIKDictLoader::dict_quen_text},
    {"stop", ObFTDictType::DICT_IK_STOP, ObIKDictLoader::dict_stop},
  };

  for (size_t idx = 0; idx < sizeof(dicts) / sizeof(dicts[0]); idx++) {
    const DictInfo &dict = dicts[idx];
    ObFTDictDesc desc(dict.name, dict.type,
                      ObCharsetType::CHARSET_UTF8MB4,
                      ObCollationType::CS_TYPE_UTF8MB4_BIN);

    ObIKDictLoader::RawDict raw_dict = dict.loader();
    ObIKDictIterator iter(raw_dict);
    ret = iter.init();
    ASSERT_EQ(OB_SUCCESS, ret);

    // Build file path: test_dir/dict_name.dat
    char file_path[4096];
    snprintf(file_path, sizeof(file_path), "%s/%s.dat", test_dir_.c_str(), dict.name);

    ret = ObFTRangeDict::build_and_serialize(file_path, desc, iter);
    ASSERT_EQ(OB_SUCCESS, ret);

    // Verify file exists
    ASSERT_EQ(0, access(file_path, F_OK)) << "File not found: " << file_path;

    // Check file size is non-zero
    struct stat st;
    ASSERT_EQ(0, stat(file_path, &st));
    ASSERT_GT(st.st_size, 0) << "File is empty: " << file_path;

    printf("%s dict: serialized to %s (size: %ld bytes)\n", dict.name, file_path, st.st_size);
  }

  printf("Output directory: %s\n", test_dir_.c_str());
}

} // end namespace storage
} // end namespace oceanbase

int main(int argc, char **argv)
{
  system("rm -rf test_ft_range_dict_serialize.log");
  OB_LOGGER.set_file_name("test_ft_range_dict_serialize.log", true);
  OB_LOGGER.set_log_level("INFO");
  testing::InitGoogleTest(&argc, argv);

  return RUN_ALL_TESTS();
}
