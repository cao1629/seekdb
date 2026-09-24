// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "device_test_registry.h"

#include "lib/allocator/ob_malloc.h"
#include "share/ob_errno.h"
#include "share/mysql_errno.h"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

namespace seekdb::ios_test {
namespace {

using oceanbase::common::OB_INVALID_ARGUMENT;
using oceanbase::common::OB_MALLOC_BACKEND_JEMALLOC;
using oceanbase::common::OB_MALLOC_BACKEND_OBMALLOC;
using oceanbase::common::OB_MALLOC_BACKEND_UNKNOWN;
using oceanbase::common::ObMallocBackend;
using oceanbase::common::ObMemAttr;
using oceanbase::common::get_ob_malloc_backend;
using oceanbase::common::is_jemalloc_backend;
using oceanbase::common::is_ob_malloc_backend;
using oceanbase::common::jemalloc_memalign;
using oceanbase::common::ob_error_name;
using oceanbase::common::ob_free;
using oceanbase::common::ob_malloc;
using oceanbase::common::ob_malloc_backend_env_name;
using oceanbase::common::ob_malloc_usable_size;
using oceanbase::common::ob_mysql_errno;
using oceanbase::common::ob_realloc;
using oceanbase::common::ob_sqlstate;
using oceanbase::common::parse_ob_malloc_backend;

constexpr std::size_t ALLOCATION_SIZE = 128;
constexpr std::size_t REALLOCATION_SIZE = 256;
constexpr std::size_t DEVICE_ALIGNMENT = 4096;

/** Verify backend parsing and one-time process detection on the active iOS build. */
int run_allocator_backend(TestContext &context)
{
  const ObMallocBackend expected_default = OB_MALLOC_BACKEND_JEMALLOC;
  context.assert_true("environment_name", std::strcmp("MALLOC_BACKEND", ob_malloc_backend_env_name()) == 0,
                      "allocator backend environment name remains stable");
  context.assert_equal("parse_null", expected_default, parse_ob_malloc_backend(nullptr),
                       "the bundled iOS allocator is the default");
  context.assert_equal("parse_empty", expected_default, parse_ob_malloc_backend(""),
                       "an empty selection uses the bundled iOS allocator");
  context.assert_equal("parse_obmalloc", OB_MALLOC_BACKEND_OBMALLOC, parse_ob_malloc_backend("obmalloc"),
                       "the explicit obmalloc backend remains recognized");
  context.assert_equal("parse_jemalloc", OB_MALLOC_BACKEND_JEMALLOC, parse_ob_malloc_backend("jemalloc"),
                       "the bundled jemalloc backend remains recognized");
  context.assert_equal("parse_system", OB_MALLOC_BACKEND_UNKNOWN, parse_ob_malloc_backend("system"),
                       "the unsupported system backend is rejected");
  context.assert_equal("parse_glibc", OB_MALLOC_BACKEND_UNKNOWN, parse_ob_malloc_backend("glibc"),
                       "the unsupported glibc backend is rejected");
  context.assert_equal("parse_mimalloc", OB_MALLOC_BACKEND_UNKNOWN, parse_ob_malloc_backend("mimalloc"),
                       "the unsupported mimalloc backend is rejected");
  context.assert_equal("parse_other", OB_MALLOC_BACKEND_UNKNOWN, parse_ob_malloc_backend("other"),
                       "an arbitrary backend name is rejected");
  context.assert_equal("parse_invalid", OB_MALLOC_BACKEND_UNKNOWN, parse_ob_malloc_backend("invalid"),
                       "unknown allocator names are rejected");
  context.assert_true("backend_predicates",
                      is_ob_malloc_backend(OB_MALLOC_BACKEND_OBMALLOC) &&
                          !is_ob_malloc_backend(OB_MALLOC_BACKEND_JEMALLOC) &&
                          is_jemalloc_backend(OB_MALLOC_BACKEND_JEMALLOC) &&
                          !is_jemalloc_backend(OB_MALLOC_BACKEND_OBMALLOC) &&
                          !is_jemalloc_backend(OB_MALLOC_BACKEND_UNKNOWN),
                      "backend predicates distinguish supported values");

  const ObMallocBackend first = get_ob_malloc_backend();
  const char *previous_value = std::getenv(ob_malloc_backend_env_name());
  const bool had_previous_value = previous_value != nullptr;
  const std::string saved_value = had_previous_value ? previous_value : "";
  const char *replacement = first == OB_MALLOC_BACKEND_OBMALLOC ? "jemalloc" : "obmalloc";
  const int set_result = setenv(ob_malloc_backend_env_name(), replacement, 1);
  context.assert_equal("set_environment", 0, set_result, "iOS accepted the temporary backend environment value");
  context.assert_equal("detect_once", first, get_ob_malloc_backend(),
                       "backend detection remains fixed after first initialization");
  const int restore_result = had_previous_value
      ? setenv(ob_malloc_backend_env_name(), saved_value.c_str(), 1)
      : unsetenv(ob_malloc_backend_env_name());
  context.assert_equal("restore_environment", 0, restore_result,
                       "the previous backend environment value was restored");
  return context.failure_count();
}

/** Verify ordinary selected-backend allocation, resizing, usable size, and free. */
void verify_allocator_reallocation(TestContext &context)
{
  ObMemAttr attr;
  void *allocation = ob_malloc(ALLOCATION_SIZE, attr);
  context.assert_true("allocate", allocation != nullptr, "ob_malloc returned storage on the running iOS engine");
  if (allocation == nullptr) {
    return;
  }
  context.assert_true("initial_usable_size", ob_malloc_usable_size(allocation) >= ALLOCATION_SIZE,
                      "reported usable size covers the requested allocation");
  std::memset(allocation, 0x3c, ALLOCATION_SIZE);

  void *resized = ob_realloc(allocation, REALLOCATION_SIZE, attr);
  context.assert_true("reallocate", resized != nullptr, "ob_realloc grew selected-backend storage");
  if (resized == nullptr) {
    ob_free(allocation);
    return;
  }
  const auto *bytes = static_cast<const unsigned char *>(resized);
  bool prefix_preserved = true;
  for (std::size_t index = 0; index < ALLOCATION_SIZE; ++index) {
    prefix_preserved = prefix_preserved && bytes[index] == 0x3c;
  }
  context.assert_true("reallocation_preserves_prefix", prefix_preserved,
                      "reallocation preserved the original bytes");
  context.assert_true("resized_usable_size", ob_malloc_usable_size(resized) >= REALLOCATION_SIZE,
                      "reported usable size covers the enlarged allocation");
  ob_free(resized);
  context.assert_true("free_completed", true, "ob_free completed for selected-backend storage");
}

/** Run the ordinary selected-backend allocator lifecycle as a standalone case. */
int run_allocator_lifecycle(TestContext &context)
{
  verify_allocator_reallocation(context);
  return context.failure_count();
}

/** Verify the bundled iOS allocator returns storage with the requested alignment. */
int run_allocator_realloc_alignment(TestContext &context)
{
  context.assert_true("selected_backend", is_jemalloc_backend(),
                      "the running iOS engine selected bundled jemalloc");
  if (!is_jemalloc_backend()) {
    return context.failure_count();
  }
  verify_allocator_reallocation(context);
  void *allocation = jemalloc_memalign(DEVICE_ALIGNMENT, ALLOCATION_SIZE);
  context.assert_true("aligned_allocate", allocation != nullptr,
                      "bundled jemalloc created aligned storage on iOS");
  if (allocation != nullptr) {
    const auto address = reinterpret_cast<std::uintptr_t>(allocation);
    context.assert_equal("alignment", 0, static_cast<int64_t>(address % DEVICE_ALIGNMENT),
                         "the returned address satisfies the requested alignment");
    std::memset(allocation, 0x5a, ALLOCATION_SIZE);
    ob_free(allocation);
    context.assert_true("aligned_free_completed", true,
                        "ordinary selected-backend free released aligned storage");
  }
  return context.failure_count();
}

/** Verify representative OceanBase-to-MySQL error metadata in the linked runtime. */
int run_ob_error_mapping(TestContext &context)
{
  context.assert_true("error_name",
                      std::strcmp("OB_INVALID_ARGUMENT", ob_error_name(OB_INVALID_ARGUMENT)) == 0,
                      "the linked runtime exposes the expected OceanBase error name");
  context.assert_equal("mysql_errno", ER_WRONG_ARGUMENTS, ob_mysql_errno(OB_INVALID_ARGUMENT),
                       "the OceanBase error maps to the exact MySQL protocol error");
  context.assert_true("sqlstate", std::strcmp("HY000", ob_sqlstate(OB_INVALID_ARGUMENT)) == 0,
                      "the OceanBase error maps to its stable SQLSTATE");
  context.assert_equal("out_of_range_mysql_errno", -1, ob_mysql_errno(1),
                       "positive non-OceanBase codes are rejected by the map");
  context.assert_true("out_of_range_name", std::strcmp("Unknown error", ob_error_name(1)) == 0,
                      "positive non-OceanBase codes do not acquire a false name");
  return context.failure_count();
}

} // namespace

DeviceTestRegistry make_cpp_device_registry()
{
  DeviceTestRegistry registry;
  registry.add({"ios.cpp.allocator.backend", "cpp", 30, run_allocator_backend});
  registry.add({"ios.cpp.allocator.lifecycle", "cpp", 30, run_allocator_lifecycle});
  registry.add({"ios.cpp.allocator.realloc_alignment", "cpp", 30, run_allocator_realloc_alignment});
  registry.add({"ios.cpp.ob_error.mapping", "cpp", 30, run_ob_error_mapping});
  return registry;
}

DeviceTestRegistry make_device_registry()
{
  DeviceTestRegistry registry = make_smoke_registry();
  if (!registry.add_all(make_cpp_device_registry())) {
    return {};
  }
#if defined(SQL_NIO_IOS_DEVICE_TESTS)
  if (!registry.add_all(make_rust_device_registry())) {
    return {};
  }
#endif
  return registry;
}

} // namespace seekdb::ios_test
