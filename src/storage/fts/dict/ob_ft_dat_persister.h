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

#ifndef _OCEANBASE_STORAGE_FTS_DICT_OB_FT_DAT_PERSISTER_H_
#define _OCEANBASE_STORAGE_FTS_DICT_OB_FT_DAT_PERSISTER_H_

#include "lib/allocator/ob_allocator.h"
#include "lib/utility/ob_macro_utils.h"
#include "storage/fts/dict/ob_ft_dict_def.h"
#include "storage/fts/dict/ob_ft_dat_dict.h"
#include "storage/fts/dict/ob_ft_cache_container.h"

namespace oceanbase
{
namespace storage
{

// File header for DAT persistence
struct ObFTDATFileHeader
{
  static constexpr int16_t MAGIC = 0x4441;  // "DA" in little-endian
  static constexpr int16_t VERSION = 1;

  int16_t magic_;
  int16_t version_;
  uint32_t dict_type_;
  int32_t range_count_;
  int64_t total_size_;
  uint64_t checksum_;

  ObFTDATFileHeader()
      : magic_(MAGIC),
        version_(VERSION),
        dict_type_(0),
        range_count_(0),
        total_size_(0),
        checksum_(0)
  {}

  bool is_valid() const
  {
    return magic_ == MAGIC && version_ == VERSION;
  }
} __attribute__((packed));

// Per-range header in the file
struct ObFTDATRangeHeader
{
  int32_t range_id_;
  int64_t dat_size_;

  ObFTDATRangeHeader() : range_id_(0), dat_size_(0) {}
  ObFTDATRangeHeader(int32_t range_id, int64_t dat_size)
      : range_id_(range_id), dat_size_(dat_size) {}
} __attribute__((packed));

class ObFTDATPersister
{
public:
  // Serialize DAT ranges to file
  // @param file_path: path to the output file
  // @param dict_type: type of dictionary (DICT_IK_MAIN, DICT_IK_QUAN, DICT_IK_STOP)
  // @param container: container holding the DAT cache handles
  // @return OB_SUCCESS on success, error code otherwise
  static int serialize_to_file(
      const char *file_path,
      ObFTDictType dict_type,
      const ObFTCacheRangeContainer &container);

  // Deserialize DAT ranges from file
  // @param file_path: path to the input file
  // @param alloc: allocator for memory allocation
  // @param container: container to populate with loaded DAT handles
  // @return OB_SUCCESS on success, OB_ENTRY_NOT_EXIST if file not found,
  //         OB_CHECKSUM_ERROR if validation fails, error code otherwise
  static int deserialize_from_file(
      const char *file_path,
      ObIAllocator &alloc,
      ObFTCacheRangeContainer &container);

private:
  // Calculate CRC64 checksum for data block
  static uint64_t calc_checksum(const char *data, int64_t len);

  DISALLOW_COPY_AND_ASSIGN(ObFTDATPersister);
};

} //  namespace storage
} //  namespace oceanbase

#endif // _OCEANBASE_STORAGE_FTS_DICT_OB_FT_DAT_PERSISTER_H_
