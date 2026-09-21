// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "seekdb_ios.h"
#include <atomic>
#include <fcntl.h>
#include <unistd.h>
#include <curl/curl.h>
#include "lib/file/file_directory_utils.h"
#include "lib/oblog/ob_log.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "observer/ob_server.h"
#include "observer/ob_server_options.h"

using namespace oceanbase;
using namespace oceanbase::common;
using namespace oceanbase::observer;

namespace {
std::atomic<seekdb_ios_state> runtime_state{SEEKDB_IOS_IDLE};
std::atomic<bool> stop_requested{false};

/** Create engine-owned directories and configure a bounded, socket-only server. */
int prepare_runtime(const char *directory, ObServerOptions &options)
{
  int ret = options.base_dir_.assign(directory);
  if (OB_SUCC(ret)) {
    ret = FileDirectoryUtils::create_full_path(directory);
  }
  if (OB_SUCC(ret) && chdir(directory) != 0) {
    ret = OB_IO_ERROR;
  }
  for (const char *path : {"run", "etc", "log"}) {
    if (OB_SUCC(ret)) {
      ret = FileDirectoryUtils::create_full_path(path);
    }
  }
  options.in_process_ = true;
  options.nodaemon_ = true;
  const char *parameters[][2] = {
      {"memory_limit", "1G"}, {"log_disk_size", "2G"},
      {"mysql_port_mode", "disabled"}, {"sql_net_thread_count", "2"},
      {"cpu_count", "2"}};
  for (const auto &parameter : parameters) {
    if (OB_SUCC(ret)) {
      ret = options.parameters_.push_back(std::make_pair(
          ObString(parameter[0]), ObString(parameter[1])));
    }
  }
  return ret;
}

/** Start and stop the singleton without taking ownership of process signals. */
int run_runtime(ObServerOptions &options)
{
  int ret = OB_SUCCESS;
  lib::ObStackHeaderGuard stack_header_guard;
  lib::Worker worker;
  lib::Worker::set_worker_to_thread_local(&worker);
  OB_LOGGER.set_log_level(DEFAULT_LOG_LEVEL);
  OB_LOGGER.set_file_name("log/seekdb.log", true, false);
  ObPLogWriterCfg log_config;
  ObServer &server = ObServer::get_instance();
  if (OB_FAIL(server.init(options, log_config))) {
  } else if (OB_FAIL(server.start())) {
  } else {
    runtime_state.store(SEEKDB_IOS_RUNNING);
    while (!stop_requested.load()) {
      usleep(100000);
    }
    runtime_state.store(SEEKDB_IOS_STOPPING);
    server.prepare_stop();
    server.set_stop();
    ret = server.wait();
    server.destroy();
  }
  // Failed startup may leave global services initialized; retry is prohibited.
  lib::Worker::set_worker_to_thread_local(nullptr);
  return ret;
}
} // namespace

int seekdb_ios_run(const char *absolute_directory)
{
  if (absolute_directory == nullptr || absolute_directory[0] != '/') {
    return OB_INVALID_ARGUMENT;
  }
  seekdb_ios_state expected = SEEKDB_IOS_IDLE;
  if (!runtime_state.compare_exchange_strong(expected, SEEKDB_IOS_STARTING)) {
    return OB_INIT_TWICE;
  }
  int ret = OB_SUCCESS;
  bool entered_runtime = false;
  const int previous_directory = open(".", O_RDONLY);
  if (previous_directory < 0) {
    ret = OB_IO_ERROR;
  } else {
    ObServerOptions options;
    if (OB_FAIL(prepare_runtime(absolute_directory, options))) {
    } else if (curl_global_init(CURL_GLOBAL_ALL) != CURLE_OK) {
      ret = OB_ERR_UNEXPECTED;
    } else {
      entered_runtime = true;
      ret = run_runtime(options);
      // Global curl state remains available if startup left active services.
      if (OB_SUCC(ret)) {
        curl_global_cleanup();
      }
    }
    if ((!entered_runtime || OB_SUCC(ret)) && fchdir(previous_directory) != 0 && OB_SUCC(ret)) {
      ret = OB_IO_ERROR;
    }
    close(previous_directory);
  }
  runtime_state.store(OB_SUCC(ret) ? SEEKDB_IOS_STOPPED : SEEKDB_IOS_FAILED);
  return ret;
}

void seekdb_ios_request_stop(void)
{
  stop_requested.store(true);
}

seekdb_ios_state seekdb_ios_get_state(void)
{
  return runtime_state.load();
}
