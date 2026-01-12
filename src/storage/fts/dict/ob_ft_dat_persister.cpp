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

#define USING_LOG_PREFIX STORAGE_FTS

#include "storage/fts/dict/ob_ft_dat_persister.h"

#include "lib/ob_errno.h"
#include "lib/oblog/ob_log_module.h"
#include "lib/checksum/ob_crc64.h"
#include "lib/utility/ob_macro_utils.h"
#include "share/rc/ob_tenant_base.h"
#include "storage/fts/dict/ob_ft_cache.h"
#include "storage/fts/dict/ob_ft_cache_dict.h"

#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

namespace oceanbase
{
namespace storage
{

uint64_t ObFTDATPersister::calc_checksum(const char *data, int64_t len)
{
  return common::ob_crc64(data, len);
}

int ObFTDATPersister::serialize_to_file(
    const char *file_path,
    ObFTDictType dict_type,
    const ObFTCacheRangeContainer &container)
{
  int ret = OB_SUCCESS;
  int fd = -1;

  if (OB_ISNULL(file_path)) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("file_path is null", K(ret));
  } else if (container.get_handles().size() <= 0) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("container is empty", K(ret));
  } else if ((fd = ::open(file_path, O_WRONLY | O_CREAT | O_TRUNC, 0644)) < 0) {
    ret = OB_IO_ERROR;
    LOG_WARN("failed to open file for writing", K(ret), K(file_path), K(errno));
  } else {
    // Prepare file header
    ObFTDATFileHeader header;
    header.dict_type_ = static_cast<uint32_t>(dict_type);
    header.range_count_ = static_cast<int32_t>(container.get_handles().size());

    // Write placeholder header (will update later with checksum and total_size)
    ssize_t written = ::write(fd, &header, sizeof(header));
    if (written != sizeof(header)) {
      ret = OB_IO_ERROR;
      LOG_WARN("failed to write file header", K(ret), K(written), K(errno));
    }

    // Calculate checksum as we write ranges
    uint64_t data_checksum = 0;
    int32_t range_id = 0;

    // Write each range
    const ObList<ObFTCacheRangeHandle *, ObIAllocator> &handles = container.get_handles();
    for (ObList<ObFTCacheRangeHandle *, ObIAllocator>::const_iterator iter = handles.begin();
         OB_SUCC(ret) && iter != handles.end();
         ++iter, ++range_id) {
      const ObFTCacheRangeHandle *handle = *iter;
      if (OB_ISNULL(handle) || OB_ISNULL(handle->value_) || OB_ISNULL(handle->value_->dat_block_)) {
        ret = OB_ERR_UNEXPECTED;
        LOG_WARN("invalid handle or value", K(ret), K(range_id));
      } else {
        const ObFTDAT *dat = handle->value_->dat_block_;
        int64_t dat_size = dat->mem_block_size_;

        // Write range header
        ObFTDATRangeHeader range_header(range_id, dat_size);
        written = ::write(fd, &range_header, sizeof(range_header));
        if (written != sizeof(range_header)) {
          ret = OB_IO_ERROR;
          LOG_WARN("failed to write range header", K(ret), K(range_id), K(written), K(errno));
        } else {
          // Write DAT block
          written = ::write(fd, dat, dat_size);
          if (written != dat_size) {
            ret = OB_IO_ERROR;
            LOG_WARN("failed to write dat block", K(ret), K(range_id), K(dat_size), K(written), K(errno));
          } else {
            // Update checksum
            data_checksum = common::ob_crc64(data_checksum, reinterpret_cast<const char *>(dat), dat_size);
          }
        }
      }
    }

    // Update header with final values
    if (OB_SUCC(ret)) {
      off_t file_size = ::lseek(fd, 0, SEEK_END);
      header.total_size_ = file_size;
      header.checksum_ = data_checksum;

      // Seek to beginning and rewrite header
      if (::lseek(fd, 0, SEEK_SET) != 0) {
        ret = OB_IO_ERROR;
        LOG_WARN("failed to seek to beginning", K(ret), K(errno));
      } else {
        written = ::write(fd, &header, sizeof(header));
        if (written != sizeof(header)) {
          ret = OB_IO_ERROR;
          LOG_WARN("failed to rewrite file header", K(ret), K(written), K(errno));
        }
      }
    }

    // Sync to disk
    if (OB_SUCC(ret)) {
      if (::fsync(fd) != 0) {
        ret = OB_IO_ERROR;
        LOG_WARN("failed to fsync", K(ret), K(errno));
      }
    }

    // Close file
    if (fd >= 0) {
      ::close(fd);
    }

    if (OB_SUCC(ret)) {
      LOG_INFO("successfully serialized DAT to file",
               K(file_path), K(dict_type), K(header.range_count_), K(header.total_size_));
    }
  }

  return ret;
}

int ObFTDATPersister::deserialize_from_file(
    const char *file_path,
    ObIAllocator &alloc,
    ObFTCacheRangeContainer &container)
{
  int ret = OB_SUCCESS;
  int fd = -1;

  if (OB_ISNULL(file_path)) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("file_path is null", K(ret));
  } else if ((fd = ::open(file_path, O_RDONLY)) < 0) {
    if (errno == ENOENT) {
      ret = OB_ENTRY_NOT_EXIST;
      LOG_INFO("DAT file does not exist", K(file_path));
    } else {
      ret = OB_IO_ERROR;
      LOG_WARN("failed to open file for reading", K(ret), K(file_path), K(errno));
    }
  } else {
    // Read and validate file header
    ObFTDATFileHeader header;
    ssize_t bytes_read = ::read(fd, &header, sizeof(header));
    if (bytes_read != sizeof(header)) {
      ret = OB_IO_ERROR;
      LOG_WARN("failed to read file header", K(ret), K(bytes_read), K(errno));
    } else if (!header.is_valid()) {
      ret = OB_CHECKSUM_ERROR;
      LOG_WARN("invalid file header", K(ret), K(header.magic_), K(header.version_));
    } else {
      // Read each range and put into cache
      uint64_t computed_checksum = 0;
      ObFTDictType dict_type = static_cast<ObFTDictType>(header.dict_type_);

      for (int32_t i = 0; OB_SUCC(ret) && i < header.range_count_; ++i) {
        // Read range header
        ObFTDATRangeHeader range_header;
        bytes_read = ::read(fd, &range_header, sizeof(range_header));
        if (bytes_read != sizeof(range_header)) {
          ret = OB_IO_ERROR;
          LOG_WARN("failed to read range header", K(ret), K(i), K(bytes_read), K(errno));
        } else if (range_header.dat_size_ <= 0) {
          ret = OB_ERR_UNEXPECTED;
          LOG_WARN("invalid dat_size in range header", K(ret), K(i), K(range_header.dat_size_));
        } else {
          // Allocate memory for DAT block
          ObFTDAT *dat = static_cast<ObFTDAT *>(alloc.alloc(range_header.dat_size_));
          if (OB_ISNULL(dat)) {
            ret = OB_ALLOCATE_MEMORY_FAILED;
            LOG_WARN("failed to allocate memory for DAT", K(ret), K(range_header.dat_size_));
          } else {
            // Read DAT block
            bytes_read = ::read(fd, dat, range_header.dat_size_);
            if (bytes_read != range_header.dat_size_) {
              ret = OB_IO_ERROR;
              LOG_WARN("failed to read DAT block", K(ret), K(i), K(bytes_read), K(range_header.dat_size_), K(errno));
              alloc.free(dat);
            } else {
              // Update checksum
              computed_checksum = common::ob_crc64(computed_checksum,
                                                   reinterpret_cast<const char *>(dat),
                                                   range_header.dat_size_);

              // Put into cache and add to container
              ObFTCacheRangeHandle *handle = nullptr;
              if (OB_FAIL(container.fetch_info_for_dict(handle))) {
                LOG_WARN("failed to fetch handle from container", K(ret));
                alloc.free(dat);
              } else {
                // Create cache key and value, then put into cache
                ObFTDictDesc desc(ObString(), dict_type, ObCharsetType::CHARSET_UTF8MB4,
                                  ObCollationType::CS_TYPE_UTF8MB4_BIN);
                if (OB_FAIL(ObFTCacheDict::make_and_fetch_cache_entry(
                        desc, dat, range_header.dat_size_, range_header.range_id_,
                        handle->value_, handle->handle_))) {
                  LOG_WARN("failed to put DAT into cache", K(ret), K(i));
                  alloc.free(dat);
                } else {
                  handle->type_ = dict_type;
                }
              }
            }
          }
        }
      }

      // Validate checksum
      if (OB_SUCC(ret) && computed_checksum != header.checksum_) {
        ret = OB_CHECKSUM_ERROR;
        LOG_WARN("checksum mismatch", K(ret), K(computed_checksum), K(header.checksum_));
        container.reset();
      }

      if (OB_SUCC(ret)) {
        LOG_INFO("successfully deserialized DAT from file",
                 K(file_path), K(dict_type), K(header.range_count_));
      }
    }

    // Close file
    if (fd >= 0) {
      ::close(fd);
    }
  }

  return ret;
}

} //  namespace storage
} //  namespace oceanbase
