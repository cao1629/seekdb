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

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <utility>

namespace seekdb::ios_test {
namespace {

constexpr int CASE_TIMEOUT_RESULT = 124;

/** Match a complete string against a small deterministic glob expression. */
bool glob_matches(const std::string &pattern, const std::string &value)
{
  std::size_t pattern_index = 0;
  std::size_t value_index = 0;
  std::size_t star_index = std::string::npos;
  std::size_t retry_value_index = 0;
  while (value_index < value.size()) {
    if (pattern_index < pattern.size() &&
        (pattern[pattern_index] == '?' || pattern[pattern_index] == value[value_index])) {
      ++pattern_index;
      ++value_index;
    } else if (pattern_index < pattern.size() && pattern[pattern_index] == '*') {
      star_index = pattern_index++;
      retry_value_index = value_index;
    } else if (star_index != std::string::npos) {
      pattern_index = star_index + 1;
      value_index = ++retry_value_index;
    } else {
      return false;
    }
  }
  while (pattern_index < pattern.size() && pattern[pattern_index] == '*') {
    ++pattern_index;
  }
  return pattern_index == pattern.size();
}

/** Execute the built-in registry assertion without touching product state. */
int run_smoke_case(TestContext &context)
{
  return context.assert_true("device_execution", true, "device registry callback executed") ? 0 : 1;
}

} // namespace

TestContext::TestContext(DeviceEvidenceWriter &evidence, std::string case_id)
    : evidence_(evidence), case_id_(std::move(case_id)), failure_count_(0)
{}

bool TestContext::assert_true(const std::string &name, bool condition, const std::string &diagnostic)
{
  if (!condition) {
    ++failure_count_;
  }
  const bool recorded = evidence_.append({
      {"event", DeviceEvidenceWriter::json_string("assertion")},
      {"case_id", DeviceEvidenceWriter::json_string(case_id_)},
      {"assertion", DeviceEvidenceWriter::json_string(name)},
      {"passed", condition ? "true" : "false"},
      {"diagnostic", DeviceEvidenceWriter::json_string(diagnostic)},
  });
  if (!recorded) {
    ++failure_count_;
  }
  return condition && recorded;
}

bool TestContext::assert_equal(
    const std::string &name, int64_t expected, int64_t actual, const std::string &diagnostic)
{
  const std::string detail = diagnostic + "; expected=" + std::to_string(expected) +
                             ", actual=" + std::to_string(actual);
  return assert_true(name, expected == actual, detail);
}

int TestContext::failure_count() const
{
  return failure_count_;
}

bool DeviceTestRegistry::add(DeviceTestCase test_case)
{
  if (test_case.id.empty() || test_case.suite.empty() || test_case.timeout_seconds == 0 || test_case.function == nullptr ||
      std::any_of(cases_.begin(), cases_.end(), [&](const DeviceTestCase &item) { return item.id == test_case.id; })) {
    return false;
  }
  cases_.push_back(std::move(test_case));
  std::sort(cases_.begin(), cases_.end(),
            [](const DeviceTestCase &left, const DeviceTestCase &right) { return left.id < right.id; });
  return true;
}

bool DeviceTestRegistry::add_all(const DeviceTestRegistry &other)
{
  for (const DeviceTestCase &test_case : other.cases_) {
    if (!add(test_case)) {
      return false;
    }
  }
  return true;
}

std::vector<const DeviceTestCase *> DeviceTestRegistry::select(
    const std::string &suite, const std::string &filter) const
{
  std::vector<const DeviceTestCase *> selected;
  const std::string effective_filter = filter.empty() ? "*" : filter;
  for (const DeviceTestCase &test_case : cases_) {
    if (test_case.suite == suite && glob_matches(effective_filter, test_case.id)) {
      selected.push_back(&test_case);
    }
  }
  return selected;
}

std::vector<std::string> DeviceTestRegistry::case_ids() const
{
  std::vector<std::string> ids;
  ids.reserve(cases_.size());
  for (const DeviceTestCase &test_case : cases_) {
    ids.push_back(test_case.id);
  }
  return ids;
}

DeviceTestRegistry make_smoke_registry()
{
  DeviceTestRegistry registry;
  registry.add({"ios.registry.smoke", "smoke", 30, run_smoke_case});
  return registry;
}

int run_device_suite(const DeviceTestRegistry &registry, const std::string &suite, const std::string &filter,
                     const std::string &run_id, const std::string &build_id, const std::string &evidence_path)
{
  if (suite.empty() || run_id.empty() || build_id.empty() || evidence_path.empty()) {
    return 2;
  }
  DeviceEvidenceWriter evidence(evidence_path, run_id, build_id);
  if (!evidence.good()) {
    return 3;
  }
  const std::vector<const DeviceTestCase *> selected = registry.select(suite, filter);
  std::vector<std::string> selected_ids;
  selected_ids.reserve(selected.size());
  for (const DeviceTestCase *test_case : selected) {
    selected_ids.push_back(test_case->id);
  }
  if (!evidence.append({
          {"event", DeviceEvidenceWriter::json_string("run_start")},
          {"suite", DeviceEvidenceWriter::json_string(suite)},
          {"filter", DeviceEvidenceWriter::json_string(filter)},
          {"registry_case_ids", DeviceEvidenceWriter::json_string_array(registry.case_ids())},
          {"selected_case_ids", DeviceEvidenceWriter::json_string_array(selected_ids)},
      }) || selected.empty()) {
    return 4;
  }

  int run_result = 0;
  int completed_count = 0;
  for (const DeviceTestCase *test_case : selected) {
    if (!evidence.append({
            {"event", DeviceEvidenceWriter::json_string("case_start")},
            {"case_id", DeviceEvidenceWriter::json_string(test_case->id)},
            {"timeout_seconds", std::to_string(test_case->timeout_seconds)},
        })) {
      return 5;
    }
    std::mutex deadline_mutex;
    std::condition_variable deadline_condition;
    bool callback_finished = false;
    std::thread watchdog([&] {
      std::unique_lock<std::mutex> lock(deadline_mutex);
      if (deadline_condition.wait_for(lock, std::chrono::seconds(test_case->timeout_seconds),
                                      [&] { return callback_finished; })) {
        return;
      }
      lock.unlock();
      evidence.append({
          {"event", DeviceEvidenceWriter::json_string("assertion")},
          {"case_id", DeviceEvidenceWriter::json_string(test_case->id)},
          {"assertion", DeviceEvidenceWriter::json_string("case_timeout")},
          {"passed", "false"},
          {"diagnostic", DeviceEvidenceWriter::json_string("case exceeded its timeout_seconds deadline")},
      });
      evidence.append({
          {"event", DeviceEvidenceWriter::json_string("case_end")},
          {"case_id", DeviceEvidenceWriter::json_string(test_case->id)},
          {"result", std::to_string(CASE_TIMEOUT_RESULT)},
      });
      evidence.append({
          {"event", DeviceEvidenceWriter::json_string("run_complete")},
          {"result", std::to_string(CASE_TIMEOUT_RESULT)},
          {"selected_count", std::to_string(selected.size())},
          {"completed_count", std::to_string(completed_count + 1)},
      });
      std::_Exit(CASE_TIMEOUT_RESULT);
    });
    TestContext context(evidence, test_case->id);
    const int callback_result = test_case->function(context);
    {
      std::lock_guard<std::mutex> lock(deadline_mutex);
      callback_finished = true;
    }
    deadline_condition.notify_one();
    watchdog.join();
    const int case_result = callback_result == 0 && context.failure_count() == 0 ? 0 :
                            (callback_result != 0 ? callback_result : context.failure_count());
    if (!evidence.append({
            {"event", DeviceEvidenceWriter::json_string("case_end")},
            {"case_id", DeviceEvidenceWriter::json_string(test_case->id)},
            {"result", std::to_string(case_result)},
        })) {
      return 6;
    }
    ++completed_count;
    if (case_result != 0 && run_result == 0) {
      run_result = case_result;
    }
  }
  if (!evidence.append({
          {"event", DeviceEvidenceWriter::json_string("run_complete")},
          {"result", std::to_string(run_result)},
          {"selected_count", std::to_string(selected.size())},
          {"completed_count", std::to_string(completed_count)},
      })) {
    return 7;
  }
  return run_result;
}

} // namespace seekdb::ios_test
