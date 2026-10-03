// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "device_evidence.h"

#include <cstdint>
#include <string>
#include <vector>

namespace seekdb::ios_test {

/** Provide one device case with assertion recording bound to its stable ID. */
class TestContext {
public:
  /** Bind assertions to the current case and evidence writer. */
  TestContext(DeviceEvidenceWriter &evidence, std::string case_id);

  /** Record a named boolean assertion and return the asserted condition. */
  bool assert_true(const std::string &name, bool condition, const std::string &diagnostic);

  /** Record integer equality without terminating the remaining case assertions. */
  bool assert_equal(const std::string &name, int64_t expected, int64_t actual, const std::string &diagnostic);

  /** Return the number of failed assertions recorded by this case. */
  int failure_count() const;

private:
  DeviceEvidenceWriter &evidence_;
  std::string case_id_;
  int failure_count_;
};

using DeviceTestFunction = int (*)(TestContext &context);

/** Describe a stable device test registration and its per-case timeout budget. */
struct DeviceTestCase {
  std::string id;
  std::string suite;
  uint32_t timeout_seconds;
  DeviceTestFunction function;
};

/** Own a deterministic set of uniquely identified device test registrations. */
class DeviceTestRegistry {
public:
  /** Add a valid unique case, returning false for invalid or duplicate registrations. */
  bool add(DeviceTestCase test_case);

  /** Add every case from another registry, returning false on any conflict. */
  bool add_all(const DeviceTestRegistry &other);

  /** Select cases by exact suite and a glob filter supporting '*' and '?'. */
  std::vector<const DeviceTestCase *> select(const std::string &suite, const std::string &filter) const;

  /** Return every registered stable ID in deterministic lexical order. */
  std::vector<std::string> case_ids() const;

private:
  std::vector<DeviceTestCase> cases_;
};

/** Create the built-in smoke registry that proves device execution and assertion capture. */
DeviceTestRegistry make_smoke_registry();

/** Create the device registry for iOS-supported C++ behavior. */
DeviceTestRegistry make_cpp_device_registry();

/** Create lossless active mysqltest cases backed by exact result transcripts. */
DeviceTestRegistry make_mysqltest_device_registry();

#if defined(SQL_NIO_IOS_DEVICE_TESTS)
/** Create the device registry backed by the test-only Rust C ABI. */
DeviceTestRegistry make_rust_device_registry();
#endif

/** Create vector recovery and bounded memory pressure device cases. */
DeviceTestRegistry make_extended_device_registry();

/** Create the complete built-in registry used by the signed test App. */
DeviceTestRegistry make_device_registry();

/** Run a selected suite and return nonzero after any registration, evidence, or case failure. */
int run_device_suite(const DeviceTestRegistry &registry, const std::string &suite, const std::string &filter,
                     const std::string &run_id, const std::string &build_id, const std::string &evidence_path);

} // namespace seekdb::ios_test
