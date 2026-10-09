// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "device_evidence.h"

#include <cerrno>

namespace seekdb::ios_test {
namespace {

/** Return whether a byte is a UTF-8 continuation byte. */
bool is_continuation(unsigned char value)
{
  return (value & 0xc0) == 0x80;
}

/** Return the valid UTF-8 sequence length at an index, or zero for a malformed leading byte. */
std::size_t utf8_sequence_length(const std::string &value, std::size_t index)
{
  const auto byte = [&](std::size_t offset) { return static_cast<unsigned char>(value[index + offset]); };
  const std::size_t remaining = value.size() - index;
  const unsigned char first = byte(0);
  if (first < 0x80) {
    return 1;
  }
  if (first >= 0xc2 && first <= 0xdf && remaining >= 2 && is_continuation(byte(1))) {
    return 2;
  }
  if (first >= 0xe0 && first <= 0xef && remaining >= 3 && is_continuation(byte(2))) {
    const unsigned char second = byte(1);
    const bool valid_second = (first == 0xe0 && second >= 0xa0 && second <= 0xbf) ||
                              (first == 0xed && second >= 0x80 && second <= 0x9f) ||
                              (((first >= 0xe1 && first <= 0xec) || (first >= 0xee && first <= 0xef)) &&
                               is_continuation(second));
    return valid_second ? 3 : 0;
  }
  if (first >= 0xf0 && first <= 0xf4 && remaining >= 4 &&
      is_continuation(byte(2)) && is_continuation(byte(3))) {
    const unsigned char second = byte(1);
    const bool valid_second = (first == 0xf0 && second >= 0x90 && second <= 0xbf) ||
                              (first == 0xf4 && second >= 0x80 && second <= 0x8f) ||
                              (first >= 0xf1 && first <= 0xf3 && is_continuation(second));
    return valid_second ? 4 : 0;
  }
  return 0;
}

} // namespace

DeviceEvidenceWriter::DeviceEvidenceWriter(
    const std::string &path, const std::string &run_id, const std::string &build_id)
    : file_(std::fopen(path.c_str(), "a")), run_id_(run_id), build_id_(build_id), failed_(file_ == nullptr)
{}

DeviceEvidenceWriter::~DeviceEvidenceWriter()
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (file_ != nullptr) {
    if (std::fflush(file_) != 0 || std::fclose(file_) != 0) {
      failed_ = true;
    }
  }
}

bool DeviceEvidenceWriter::good() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return file_ != nullptr && !failed_;
}

bool DeviceEvidenceWriter::append(const std::vector<std::pair<std::string, std::string>> &fields)
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (file_ == nullptr || failed_) {
    return false;
  }
  std::string record = "{\"run_id\":" + json_string(run_id_) + ",\"build_id\":" + json_string(build_id_) +
                       ",\"origin\":\"device\"";
  for (const auto &field : fields) {
    record += "," + json_string(field.first) + ":" + field.second;
  }
  record += "}\n";
  if (std::fwrite(record.data(), 1, record.size(), file_) != record.size() || std::fflush(file_) != 0) {
    failed_ = true;
  }
  return !failed_;
}

std::string DeviceEvidenceWriter::json_string(const std::string &value)
{
  std::string encoded = "\"";
  for (std::size_t index = 0; index < value.size();) {
    const unsigned char character = static_cast<unsigned char>(value[index]);
    switch (character) {
      case '\"': encoded += "\\\""; ++index; break;
      case '\\': encoded += "\\\\"; ++index; break;
      case '\b': encoded += "\\b"; ++index; break;
      case '\f': encoded += "\\f"; ++index; break;
      case '\n': encoded += "\\n"; ++index; break;
      case '\r': encoded += "\\r"; ++index; break;
      case '\t': encoded += "\\t"; ++index; break;
      default:
        if (character < 0x20) {
          static constexpr char digits[] = "0123456789abcdef";
          encoded += "\\u00";
          encoded += digits[(character >> 4) & 0x0f];
          encoded += digits[character & 0x0f];
          ++index;
        } else if (character < 0x80) {
          encoded += static_cast<char>(character);
          ++index;
        } else {
          const std::size_t length = utf8_sequence_length(value, index);
          if (length == 0) {
            encoded += "\\ufffd";
            ++index;
          } else {
            encoded.append(value, index, length);
            index += length;
          }
        }
    }
  }
  return encoded + "\"";
}

std::string DeviceEvidenceWriter::json_string_array(const std::vector<std::string> &values)
{
  std::string encoded = "[";
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) {
      encoded += ",";
    }
    encoded += json_string(values[index]);
  }
  return encoded + "]";
}

} // namespace seekdb::ios_test
