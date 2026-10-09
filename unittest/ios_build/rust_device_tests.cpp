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

#include "device_test_registry.h"

#include "nio.h"

#include <array>
#include <cstring>
#include <string>

namespace seekdb::ios_test {
namespace {

constexpr std::array<const char *, 5> RUST_CASE_IDS = {
    "ios.rust.cert.rejects_truncated_certificate",
    "ios.rust.cert.formats_display_name_for_sql_account",
    "ios.rust.tls.exposes_sql_cipher_names",
    "ios.rust.device.intentional_panic",
    "ios.rust.device.panic_continuation",
};

/** Run one fixed Rust ABI index and translate its bounded result into evidence. */
int run_rust_case(TestContext &context, uint32_t index, bool expected_panic)
{
  NioDeviceTestCaseInfo info{};
  const uint32_t info_status = nio_device_test_case_info(index, &info, sizeof(info));
  context.assert_equal("case_info_status", NIO_DEVICE_TEST_OK, info_status,
                       "Rust returned metadata for the fixed registry index");
  context.assert_true("case_id", info_status == NIO_DEVICE_TEST_OK &&
      std::strcmp(info.id, RUST_CASE_IDS[index]) == 0,
      "Rust and C++ registry IDs match");

  NioDeviceTestResult result{};
  const uint32_t status = nio_device_test_run(index, &result, sizeof(result));
  const uint32_t expected = expected_panic ? NIO_DEVICE_TEST_PANIC : NIO_DEVICE_TEST_OK;
  context.assert_equal("run_status", expected, status,
                       std::string(reinterpret_cast<const char *>(result.diagnostic), result.diagnostic_len));
  context.assert_true("bounded_diagnostic", result.diagnostic_len < sizeof(result.diagnostic),
                      "Rust diagnostic remains NUL-terminated inside the fixed result");
  return context.failure_count();
}

int run_cert_rejects(TestContext &context) { return run_rust_case(context, 0, false); }
int run_cert_formats(TestContext &context) { return run_rust_case(context, 1, false); }
int run_tls_names(TestContext &context) { return run_rust_case(context, 2, false); }
int run_intentional_panic(TestContext &context) { return run_rust_case(context, 3, true); }
/** Prepare panic containment in this invocation before checking continuation. */
int run_panic_continuation(TestContext &context)
{
  run_rust_case(context, 3, true);
  return run_rust_case(context, 4, false);
}

} // namespace

DeviceTestRegistry make_rust_device_registry()
{
  DeviceTestRegistry registry;
  if (nio_device_test_count() != RUST_CASE_IDS.size()) {
    return {};
  }
  registry.add({"ios.rust.cert.rejects_truncated_certificate", "rust", 30, run_cert_rejects});
  registry.add({"ios.rust.cert.formats_display_name_for_sql_account", "rust", 30, run_cert_formats});
  registry.add({"ios.rust.tls.exposes_sql_cipher_names", "rust", 30, run_tls_names});
  registry.add({"ios.rust.device.intentional_panic", "rust", 30, run_intentional_panic});
  registry.add({"ios.rust.device.panic_continuation", "rust", 30, run_panic_continuation});
  return registry;
}

} // namespace seekdb::ios_test
