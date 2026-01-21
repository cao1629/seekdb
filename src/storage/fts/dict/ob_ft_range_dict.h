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

#ifndef _OCEANBASE_STORAGE_FTS_DICT_OB_FT_RANGE_DICT_H_
#define _OCEANBASE_STORAGE_FTS_DICT_OB_FT_RANGE_DICT_H_

#include "lib/alloc/alloc_struct.h"
#include "lib/allocator/ob_allocator.h"
#include "lib/allocator/page_arena.h"
#include "lib/container/ob_vector.h"
#include "lib/utility/ob_macro_utils.h"
#include "share/rc/ob_tenant_base.h"
#include "storage/fts/dict/ob_ft_cache_container.h"
#include "storage/fts/dict/ob_ft_dict.h"
#include "storage/fts/dict/ob_ft_dict_def.h"
#include "storage/fts/dict/ob_ft_dict_iterator.h"

namespace oceanbase
{
namespace storage
{
class ObFTRangeDict final : public ObIFTDict
{
public:
  ObFTRangeDict(ObIAllocator &alloc,
                ObFTCacheRangeContainer *range_container,
                const ObFTDictDesc &desc)
      : is_inited_(false), desc_(desc), range_alloc_(lib::ObMemAttr(MTL_ID(), "Range Dict")),
        range_dicts_(&range_alloc_), range_container_(range_container)
  {
  }
  ~ObFTRangeDict() override{};

public:
  struct ObFTRange final
  {
    ObFTSingleWord start_;
    ObFTSingleWord end_;
    ObIFTDict *dict_; // a cache dict
  };

public:
  int init() override;
  int match(const ObString &single_word, ObDATrieHit &hit) const override;
  int match(const ObString &words, bool &is_match) const override;
  int match_with_hit(const ObString &single_word,
                     const ObDATrieHit &last_hit,
                     ObDATrieHit &hit) const override;

public:
  int build_dict_from_cache(const ObFTCacheRangeContainer &range_container);

  static int try_load_cache(const ObFTDictDesc &desc,
                            const uint32_t range_count,
                            ObFTCacheRangeContainer &range_container);
  static int build_cache(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container);

  static int build_cache_from_file(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container);

  // Build cache from binary data embedded in the executable
  // @param desc: dictionary descriptor
  // @param range_container: container to store the cache entries
  // @return OB_SUCCESS on success, error code otherwise
  static int build_cache_from_binary(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container);

  // Build cache from static variable data using ObIKDictIterator
  // @param desc: dictionary descriptor
  // @param range_container: container to store the cache entries
  // @return OB_SUCCESS on success, error code otherwise
  static int build_cache_from_variable(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container);

  // Build cache from IK dictionary static data using ObIKDictIterator
  // @param desc: dictionary descriptor
  // @param range_container: container to store the cache entries
  // @return OB_SUCCESS on success, error code otherwise
  static int build_cache_from_ik_dict(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container);

  // Build DAT for each range and serialize to separate files
  // @param dir_path: directory to write files to
  // @param desc: dictionary descriptor
  // @param iter: dictionary iterator
  // @param range_count: [out] number of ranges written
  // @return OB_SUCCESS on success, error code otherwise
  static int build_and_serialize_ranges(const char *dir_path,
                                        const ObFTDictDesc &desc,
                                        ObIFTDictIterator &iter,
                                        int32_t &range_count);

private:
  // build cache
  static int build_ranges(const ObFTDictDesc &desc,
                          ObIFTDictIterator &iter,
                          ObFTCacheRangeContainer &range_container);

  // build cache concurrently: collect words range by range, then build DATs in parallel
  static int build_ranges_concurrently(const ObFTDictDesc &desc,
                                       ObIFTDictIterator &iter,
                                       ObFTCacheRangeContainer &range_container);

  // build cache concurrently using ObThreadPool: collect words range by range, then build DATs in parallel
  static int build_ranges_concurrently_thread_pool(const ObFTDictDesc &desc,
                                                   ObIFTDictIterator &iter,
                                                   ObFTCacheRangeContainer &range_container);

  // build one range's cache
  static int build_one_range(const ObFTDictDesc &desc,
                             const int32_t range_id,
                             ObIFTDictIterator &iter,
                             ObFTCacheRangeContainer &container,
                             bool &build_next_range);

  // build one range and serialize to file
  static int serialize_one_range(const ObFTDictDesc &desc,
                                 const int32_t range_id,
                                 const char *file_path,
                                 ObIFTDictIterator &iter,
                                 bool &build_next_range);

  // Build one range from a serialized DAT file and put into cache
  // @param desc: dictionary descriptor
  // @param range_id: range identifier
  // @param file_path: path to the serialized DAT file
  // @param container: cache container to store the result
  // @return OB_SUCCESS on success, error code otherwise
  static int build_one_range_from_file(const ObFTDictDesc &desc,
                                       const int32_t range_id,
                                       const char *file_path,
                                       ObFTCacheRangeContainer &container);

  // Build all ranges from serialized DAT files and put into cache
  // @param desc: dictionary descriptor
  // @param dir_path: directory containing DAT files
  // @param range_count: number of range files to load
  // @param container: cache container to store the results
  // @return OB_SUCCESS on success, error code otherwise
  static int build_ranges_from_files(const ObFTDictDesc &desc,
                                     const char *dir_path,
                                     const int32_t range_count,
                                     ObFTCacheRangeContainer &container);

  // Build dictionary from a single serialized DAT file (one range = entire dictionary)
  // @param desc: dictionary descriptor
  // @param file_path: path to the serialized DAT file
  // @param container: cache container to store the result
  // @return OB_SUCCESS on success, error code otherwise
  static int build_from_file(const ObFTDictDesc &desc,
                             const char *file_path,
                             ObFTCacheRangeContainer &container);

  // Build DAT from entire dictionary (no range division) and serialize to a single file
  // @param file_path: path to write the DAT file
  // @param desc: dictionary descriptor
  // @param iter: dictionary iterator (all words)
  // @return OB_SUCCESS on success, error code otherwise
  static int build_and_serialize(const char *file_path,
                                 const ObFTDictDesc &desc,
                                 ObIFTDictIterator &iter);

  // Build dictionary from binary data in memory (one range = entire dictionary)
  // @param desc: dictionary descriptor
  // @param data: pointer to the DAT data in memory
  // @param data_size: size of the DAT data
  // @param container: cache container to store the result
  // @return OB_SUCCESS on success, error code otherwise
  static int build_from_memory(const ObFTDictDesc &desc,
                               const char *data,
                               const size_t data_size,
                               ObFTCacheRangeContainer &container);

private:
  void destroy()
  {
    for (int64_t i = 0; i < range_dicts_.size(); i++) {
      range_dicts_[i].dict_->~ObIFTDict(); // destroy dict
    }
    range_dicts_.reset();
    range_alloc_.reset();
  }

  int find_first_char_range(const ObString &single_word, ObIFTDict *&dict) const;

private:
  static constexpr int DEFAULT_KEY_PER_RANGE = 50000; // by estimated
  bool is_inited_;
  ObFTDictDesc desc_;
  ObArenaAllocator range_alloc_;
  ObVector<ObFTRange, ObArenaAllocator> range_dicts_;
  ObFTCacheRangeContainer *range_container_; // only used to read cache

private:
  DISALLOW_COPY_AND_ASSIGN(ObFTRangeDict);
};

} //  namespace storage
} //  namespace oceanbase

#endif // _OCEANBASE_STORAGE_FTS_DICT_OB_FT_RANGE_DICT_H_
