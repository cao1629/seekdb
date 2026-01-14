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

#include "storage/fts/dict/ob_ft_range_dict.h"

#include "lib/allocator/page_arena.h"
#include "lib/charset/ob_charset.h"
#include "lib/mysqlclient/ob_isql_client.h"
#include "lib/ob_errno.h"
#include "lib/oblog/ob_log_module.h"
#include "lib/utility/ob_macro_utils.h"
#include "lib/utility/utility.h"
#include "ob_smart_var.h"
#include "share/inner_table/ob_inner_table_schema_constants.h"
#include "storage/fts/dict/ob_ft_cache.h"
#include "storage/fts/dict/ob_ft_cache_container.h"
#include "storage/fts/dict/ob_ft_cache_dict.h"
#include "storage/fts/dict/ob_ft_dat_dict.h"
#include "storage/fts/dict/ob_ft_dict.h"
#include "storage/fts/dict/ob_ft_dict_def.h"
#include "storage/fts/dict/ob_ft_dict_iterator.h"
#include "storage/fts/dict/ob_ft_dict_table_iter.h"
#include "storage/fts/dict/ob_ft_trie.h"

#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <cstdio>

#define USING_LOG_PREFIX STORAGE_FTS

namespace oceanbase
{
namespace storage
{
int ObFTRangeDict::build_one_range(const ObFTDictDesc &desc,
                                   const int32_t range_id,
                                   ObIFTDictIterator &iter,
                                   ObFTCacheRangeContainer &container,
                                   bool &build_next_range)
{
  int ret = OB_SUCCESS;
  build_next_range = true;

  ObArenaAllocator tmp_alloc(lib::ObMemAttr(MTL_ID(), "Temp trie"));

  ObFTDATBuilder<void> builder(tmp_alloc);
  storage::ObFTTrie<void> trie(tmp_alloc, desc.coll_type_);

  int count = 0;
  bool range_end = false;

  int64_t first_char_len = 0;
  ObFTSingleWord end_char;
  ObFTSingleWord start_char;

  ObFTDAT *dat_buff = nullptr;
  size_t buffer_size = 0;

  // read one range of words from __ft_dict_ik_utf8, populate trie
  while (OB_SUCC(ret) && !range_end) {
    ObString key;
    if (OB_FAIL(iter.get_key(key))) {
      LOG_WARN("Failed to get key", K(ret));
    } else if (OB_FALSE_IT(++count)) {
      // do nothing
    } else if (count >= DEFAULT_KEY_PER_RANGE
               && OB_FAIL(ObCharset::first_valid_char(desc.coll_type_,
                                                      key.ptr(),
                                                      key.length(),
                                                      first_char_len))) {
      LOG_WARN("First char is not valid.");
    } else if (DEFAULT_KEY_PER_RANGE == count
               && OB_FAIL(end_char.set_word(key.ptr(), first_char_len))) {
      LOG_WARN("Failed to record first char.", K(ret));
    } else if (count > DEFAULT_KEY_PER_RANGE
               && (end_char.get_word() != ObString(first_char_len, key.ptr()))) {
      // end of range, this key is not consumed.
      range_end = true;
    } else if (OB_FAIL(trie.insert(key, {}))) {
      LOG_WARN("Failed to insert key to trie", K(ret));
    } else if (OB_FAIL(iter.next()) && OB_ITER_END != ret) {
      LOG_WARN("Failed to step to next word entry.", K(ret));
    }
  }

  if (OB_ITER_END == ret) {
    build_next_range = false; // no more data
    ret = OB_SUCCESS;
  }

  // trie -> DAT Cache
  ObFTCacheRangeHandle *info = nullptr;

  if (OB_FAIL(ret)) {
    // to do clean up
  } else if (OB_FAIL(builder.init(trie))) {
    LOG_WARN("Failed to build dat.", K(ret));
  } else if (OB_FAIL(builder.build_from_trie(trie))) {
    LOG_WARN("Failed to build datrie.", K(ret));
  } else if (OB_FAIL(builder.get_mem_block(dat_buff, buffer_size))) {  // populate dat_buff and buffer_size
    LOG_WARN("Failed to get mem block.", K(ret));
  } else if (OB_FAIL(container.fetch_info_for_dict(info))) { // populate "info" for this range 
    LOG_WARN("Failed to fetch info for dict.", K(ret));
  } else if (OB_FAIL(ObFTCacheDict::make_and_fetch_cache_entry(desc, // add this DAT to ObGlobalKVCache
                                                               dat_buff,
                                                               buffer_size,
                                                               range_id,
                                                               info->value_,
                                                               info->handle_))) {
    LOG_WARN("Failed to put dict into kv cache");
  } else {
    // okay
  }

  // free memory for temporary trie and DAT.
  tmp_alloc.reset();

  return ret;
}

int ObFTRangeDict::init()
{
  int ret = OB_SUCCESS;
  if (is_inited_) {
    ret = OB_INIT_TWICE;
  } else {
    if (OB_FAIL(build_dict_from_cache(*range_container_))) {
      LOG_WARN("Failed to build dict from cache.", K(ret));
    }
    is_inited_ = true;
  }
  return ret;
}
int ObFTRangeDict::build_ranges(const ObFTDictDesc &desc,
                                ObIFTDictIterator &iter,
                                ObFTCacheRangeContainer &range_container)
{
  int ret = OB_SUCCESS;

  bool build_next_range = true;

  int32_t range_id = 0;
  while (OB_SUCC(ret) && build_next_range) {
    if (OB_FAIL(ObFTRangeDict::build_one_range(desc,
                                               range_id++,
                                               iter,
                                               range_container,
                                               build_next_range))) {
      LOG_WARN("fail to build range", K(ret));
    }
  }
  return ret;
}

int ObFTRangeDict::match(const ObString &single_word, ObDATrieHit &hit) const
{
  int ret = OB_SUCCESS;
  ObIFTDict *dict = nullptr;
  if (OB_FAIL(find_first_char_range(single_word, dict)) && OB_ENTRY_NOT_EXIST != ret) {
    LOG_WARN("Failed to find first char range.", K(ret));
  } else if (OB_ENTRY_NOT_EXIST == ret) {
    hit.set_unmatch();
    ret = OB_SUCCESS;
  } else {
    // do nothing
    hit.dict_ = dict; // set dict
    if (OB_FAIL(dict->match(single_word, hit))) {
      LOG_WARN("Failed to match.", K(ret));
    }
  }
  return ret;
}
int ObFTRangeDict::match(const ObString &words, bool &is_match) const
{
  // find first char range and find dict
  int ret = OB_SUCCESS;
  ObIFTDict *dict = nullptr;

  int64_t char_len;

  if (OB_FAIL(
          ObCharset::first_valid_char(desc_.coll_type_, words.ptr(), words.length(), char_len))) {
    LOG_WARN("Failed to find first char", K(ret));
  } else if (OB_FAIL(find_first_char_range(ObString(char_len, words.ptr()), dict))) {
    if (OB_ENTRY_NOT_EXIST == ret) {
      is_match = false;
      ret = OB_SUCCESS;
    } else {
      LOG_WARN("Failed to find first char range.", K(ret));
    }
  } else if (OB_ISNULL(dict)) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("dict is null.", K(ret));
  } else if (OB_FAIL(dict->match(words, is_match))) {
    LOG_WARN("Failed to match.", K(ret));
  }
  return ret;
}

int ObFTRangeDict::match_with_hit(const ObString &single_word,
                                  const ObDATrieHit &last_hit,
                                  ObDATrieHit &hit) const
{
  return last_hit.dict_->match_with_hit(single_word, last_hit, hit);
}

int ObFTRangeDict::find_first_char_range(const ObString &single_word, ObIFTDict *&dict) const
{
  int ret = OB_SUCCESS;
  bool found = false;
  for (int i = 0; OB_SUCC(ret) && !found && i < range_dicts_.size(); ++i) {
    if (ObCharset::strcmp(ObCollationType::CS_TYPE_UTF8MB4_BIN,
                          range_dicts_[i].start_.get_word(),
                          single_word)
            <= 0
        && ObCharset::strcmp(ObCollationType::CS_TYPE_UTF8MB4_BIN,
                             range_dicts_[i].end_.get_word(),
                             single_word)
               >= 0) {
      dict = range_dicts_[i].dict_;
      found = true;
    }
  }
  if (!found) {
    // not found, dis match
    ret = OB_ENTRY_NOT_EXIST;
  }
  return ret;
}

int ObFTRangeDict::build_dict_from_cache(const ObFTCacheRangeContainer &range_container)
{
  int ret = OB_SUCCESS;
  for (ObList<ObFTCacheRangeHandle *, ObIAllocator>::const_iterator iter = range_container.get_handles().begin();
       OB_SUCC(ret) && iter != range_container.get_handles().end();
       iter++) {
    ObFTCacheRangeHandle *ptr = *iter;
    ObFTCacheDict *dict = nullptr;
    ObFTDAT *dat = ptr->value_->dat_block_;
    if (OB_ISNULL(dict = OB_NEWx(ObFTCacheDict, &range_alloc_, ObCollationType::CS_TYPE_UTF8MB4_BIN, dat))) {
      ret = OB_ALLOCATE_MEMORY_FAILED;
      LOG_WARN("Failed to alloc memory.", K(ret));
    } else {
      ObFTRange range;
      range.start_ = dat->start_word_;
      range.end_ = dat->end_word_;
      range.dict_ = dict;
      if (OB_FAIL(range_dicts_.push_back(range))) {
        LOG_WARN("Failed to push back range dict.", K(ret));
      }
    }
  }
  return ret;
}

int ObFTRangeDict::build_cache(const ObFTDictDesc &desc, ObFTCacheRangeContainer &range_container)
{
  int ret = OB_SUCCESS;

  ObString table_name;
  switch (desc.type_) {
  case ObFTDictType::DICT_IK_MAIN: {
    table_name = ObString(share::OB_FT_DICT_IK_UTF8_TNAME);
  } break;
  case ObFTDictType::DICT_IK_QUAN: {
    table_name = ObString(share::OB_FT_QUANTIFIER_IK_UTF8_TNAME);
  } break;
  case ObFTDictType::DICT_IK_STOP: {
    table_name = ObString(share::OB_FT_STOPWORD_IK_UTF8_TNAME);
  } break;
  default:
    ret = OB_NOT_SUPPORTED;
    LOG_WARN("Not supported dict type.", K(ret));
  }

  // read words from __ft_dict_ik_utf8, andd build_ranges
  if (OB_SUCC(ret)) {
    SMART_VAR(ObISQLClient::ReadResult, result)
    {
      ObFTDictTableIter iter_table(result);
      if (OB_FAIL(iter_table.init(table_name))) {
        LOG_WARN("Failed to init iterator.", K(ret));
      } else if (OB_FAIL(ObFTRangeDict::build_ranges(desc, iter_table, range_container))) {
        LOG_WARN("Failed to build ranges.", K(ret));
      }
    }
  }

  return ret;
}

// go to global ObDictCache to see of all ranges are cached
int ObFTRangeDict::try_load_cache(const ObFTDictDesc &desc,
                                  const uint32_t range_count,
                                  ObFTCacheRangeContainer &range_container)
{
  int ret = OB_SUCCESS;
  uint64_t name = static_cast<uint64_t>(desc.type_);

  // in each loop:
  // -> poplaute key: ObDictCachekey (which tenant, which dictionary, which rnage)
  // -> range_container->fetch_info_for_dict()  
  for (int64_t i = 0; OB_SUCC(ret) && i < range_count; ++i) {
    ObDictCacheKey key(name, MTL_ID(), desc.type_, i);

    // -> append a new ObFTCacheRangeHandle node to range_container
    // -> populate this handle->value_
    // handle->key_ seems empty
    ObFTCacheRangeHandle *info = nullptr;
    if (OB_FAIL(range_container.fetch_info_for_dict(info))) {
      LOG_WARN("Failed to fetch info for dict.", K(ret));
    } else if (OB_FAIL(ObDictCache::get_instance().get_dict(key, info->value_, info->handle_))
               && OB_ENTRY_NOT_EXIST != ret) {
      LOG_WARN("Failed to get dict from kv cache.", K(ret));
    } else if (OB_ENTRY_NOT_EXIST == ret) {
      range_container.reset();
      // not found, build cache outthere
    } else if (FALSE_IT(info->type_ = desc.type_)) {
      // impossible
    }
  }

  if (OB_FAIL(ret)) {
    range_container.reset();
  }

  return ret;
}

int ObFTRangeDict::build_and_serialize_ranges(const char *dir_path,
                                              const ObFTDictDesc &desc,
                                              ObIFTDictIterator &iter,
                                              int32_t &range_count)
{
  int ret = OB_SUCCESS;
  range_count = 0;
   
  // how does OB handle this?
  if (OB_ISNULL(dir_path)) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("dir_path is null", K(ret));
  } else {
    bool build_next_range = true;
    int32_t range_id = 0;

    while (OB_SUCC(ret) && build_next_range) {
      // Build file path for this range: dir_path/dict_type_range_id.dat
      char file_path[4096];
      int n = snprintf(file_path, sizeof(file_path), "%s/%d_%d.dat",
                       dir_path, static_cast<int>(desc.type_), range_id);
      if (n < 0 || n >= static_cast<int>(sizeof(file_path))) {
        ret = OB_SIZE_OVERFLOW;
        LOG_WARN("file path too long", K(ret), K(dir_path), K(range_id));
      } else if (OB_FAIL(serialize_one_range(desc, range_id, file_path, iter, build_next_range))) {
        LOG_WARN("failed to serialize range", K(ret), K(range_id), K(file_path));
      } else {
        ++range_id;
      }
    }

    if (OB_SUCC(ret)) {
      range_count = range_id;
      LOG_INFO("successfully serialized all ranges", K(dir_path), K(range_count));
    }
  }

  return ret;
}

int ObFTRangeDict::serialize_one_range(const ObFTDictDesc &desc,
                                       const int32_t range_id,
                                       const char *file_path,
                                       ObIFTDictIterator &iter,
                                       bool &build_next_range)
{
  int ret = OB_SUCCESS;
  build_next_range = true;

  ObArenaAllocator tmp_alloc(lib::ObMemAttr(OB_SERVER_TENANT_ID, "SerializeRange"));
  ObFTDATBuilder<void> builder(tmp_alloc);
  storage::ObFTTrie<void> trie(tmp_alloc, desc.coll_type_);

  int count = 0;
  bool range_end = false;

  int64_t first_char_len = 0;
  ObFTSingleWord end_char;

  ObFTDAT *dat_buff = nullptr;
  size_t buffer_size = 0;

  // Read one range of words, populate trie (same logic as build_one_range)
  while (OB_SUCC(ret) && !range_end) {
    ObString key;
    if (OB_FAIL(iter.get_key(key))) {
      LOG_WARN("Failed to get key", K(ret));
    } else if (OB_FALSE_IT(++count)) {
      // do nothing
    } else if (count >= DEFAULT_KEY_PER_RANGE
               && OB_FAIL(ObCharset::first_valid_char(desc.coll_type_,
                                                      key.ptr(),
                                                      key.length(),
                                                      first_char_len))) {
      LOG_WARN("First char is not valid.");
    } else if (DEFAULT_KEY_PER_RANGE == count
               && OB_FAIL(end_char.set_word(key.ptr(), first_char_len))) {
      LOG_WARN("Failed to record first char.", K(ret));
    } else if (count > DEFAULT_KEY_PER_RANGE
               && (end_char.get_word() != ObString(first_char_len, key.ptr()))) {
      // end of range, this key is not consumed.
      range_end = true;
    } else if (OB_FAIL(trie.insert(key, {}))) {
      LOG_WARN("Failed to insert key to trie", K(ret));
    } else if (OB_FAIL(iter.next()) && OB_ITER_END != ret) {
      LOG_WARN("Failed to step to next word entry.", K(ret));
    }
  }

  if (OB_ITER_END == ret) {
    build_next_range = false; // no more data
    ret = OB_SUCCESS;
  }

  // Build DAT from trie
  if (OB_FAIL(ret)) {
    // error already logged
  } else if (count == 0) {
    build_next_range = false; // no data
  } else if (OB_FAIL(builder.init(trie))) {
    LOG_WARN("Failed to init DAT builder.", K(ret));
  } else if (OB_FAIL(builder.build_from_trie(trie))) {
    LOG_WARN("Failed to build DAT from trie.", K(ret));
  } else if (OB_FAIL(builder.get_mem_block(dat_buff, buffer_size))) {
    LOG_WARN("Failed to get mem block.", K(ret));
  } else {
    // Write DAT to file
    int fd = ::open(file_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
      ret = OB_IO_ERROR;
      LOG_WARN("failed to open file for writing", K(ret), K(file_path), K(errno));
    } else {
      ssize_t written = ::write(fd, dat_buff, buffer_size);
      if (written != static_cast<ssize_t>(buffer_size)) {
        ret = OB_IO_ERROR;
        LOG_WARN("failed to write DAT block", K(ret), K(range_id), K(buffer_size), K(written), K(errno));
      } else if (::fsync(fd) != 0) {
        ret = OB_IO_ERROR;
        LOG_WARN("failed to fsync", K(ret), K(errno));
      } else {
        LOG_INFO("serialized DAT range to file", K(range_id), K(file_path), K(count), K(buffer_size));
      }
      ::close(fd);
    }
  }

  tmp_alloc.reset();
  return ret;
}

} //  namespace storage
} //  namespace oceanbase
