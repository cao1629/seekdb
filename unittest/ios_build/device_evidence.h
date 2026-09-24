// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdio>
#include <string>
#include <utility>
#include <vector>

namespace seekdb::ios_test {

/** Append device-authored JSONL records and flush each record before returning. */
class DeviceEvidenceWriter {
public:
  /** Open an evidence file for append and bind all records to one run and build. */
  DeviceEvidenceWriter(const std::string &path, const std::string &run_id, const std::string &build_id);

  /** Close the evidence file after flushing any buffered bytes. */
  ~DeviceEvidenceWriter();

  DeviceEvidenceWriter(const DeviceEvidenceWriter &) = delete;
  DeviceEvidenceWriter &operator=(const DeviceEvidenceWriter &) = delete;

  /** Return whether the evidence file opened successfully and has not failed. */
  bool good() const;

  /** Append one JSON object from already encoded field values and flush it to storage. */
  bool append(const std::vector<std::pair<std::string, std::string>> &fields);

  /** Encode a string as a JSON value, including surrounding quotes. */
  static std::string json_string(const std::string &value);

  /** Encode a string sequence as a JSON array value. */
  static std::string json_string_array(const std::vector<std::string> &values);

private:
  FILE *file_;
  std::string run_id_;
  std::string build_id_;
  bool failed_;
};

} // namespace seekdb::ios_test
