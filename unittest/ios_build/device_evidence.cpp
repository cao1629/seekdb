// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "device_evidence.h"

#include <cerrno>

namespace seekdb::ios_test {

DeviceEvidenceWriter::DeviceEvidenceWriter(
    const std::string &path, const std::string &run_id, const std::string &build_id)
    : file_(std::fopen(path.c_str(), "a")), run_id_(run_id), build_id_(build_id), failed_(file_ == nullptr)
{}

DeviceEvidenceWriter::~DeviceEvidenceWriter()
{
  if (file_ != nullptr) {
    if (std::fflush(file_) != 0 || std::fclose(file_) != 0) {
      failed_ = true;
    }
  }
}

bool DeviceEvidenceWriter::good() const
{
  return file_ != nullptr && !failed_;
}

bool DeviceEvidenceWriter::append(const std::vector<std::pair<std::string, std::string>> &fields)
{
  if (!good()) {
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
  for (const unsigned char character : value) {
    switch (character) {
      case '\"': encoded += "\\\""; break;
      case '\\': encoded += "\\\\"; break;
      case '\b': encoded += "\\b"; break;
      case '\f': encoded += "\\f"; break;
      case '\n': encoded += "\\n"; break;
      case '\r': encoded += "\\r"; break;
      case '\t': encoded += "\\t"; break;
      default:
        if (character < 0x20) {
          static constexpr char digits[] = "0123456789abcdef";
          encoded += "\\u00";
          encoded += digits[(character >> 4) & 0x0f];
          encoded += digits[character & 0x0f];
        } else {
          encoded += static_cast<char>(character);
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
