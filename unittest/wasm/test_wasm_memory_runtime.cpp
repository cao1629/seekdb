/*
 * Copyright (c) 2026 OceanBase.
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
#include <cassert>
#include <cstdio>
#include <cstring>
#include <atomic>
#include <chrono>
#include <sched.h>
#include <vector>
#include <unordered_set>
#include <string>
#include <climits>
#include "share/cache/ob_kvcache_hazard_domain.h"
#include "lib/thread/threads.h"
#include "lib/thread/ob_thread_name.h"
#include "lib/allocator/ob_malloc.h"
#include "lib/resource/achunk_mgr.h"
#include "lib/hash/ob_hashset.h"
#include "lib/objectpool/ob_pool.h"
#include "lib/utility/ob_smart_call.h"

using namespace oceanbase;
using namespace oceanbase::common;
using namespace oceanbase::lib;

enum class SignedBoundary : int64_t { value = INT64_MIN };
enum class UnsignedBoundary : uint64_t { value = UINT64_MAX };

template <typename T>
static void check_number_text(const T &value, const char *expected)
{
  char buf[128]{};
  int64_t pos = 0;
  assert(databuff_print_obj(buf, sizeof(buf), pos, value) == OB_SUCCESS);
  if (std::string(buf, pos) != expected) {
    fprintf(stderr, "format mismatch: expected=%s actual=%.*s\n", expected, static_cast<int>(pos), buf);
  }
  assert(std::string(buf, pos) == expected);
  pos = 0;
  assert(databuff_print_key_obj(buf, sizeof(buf), pos, "value", true, value) == OB_SUCCESS);
  assert(std::string(buf, pos) == std::string(", value:") + expected);
  if constexpr (!std::is_volatile_v<T>) {
    pos = oceanbase::common::to_string(value, buf, sizeof(buf));
    assert(std::string(buf, pos) == expected);
  }
}

static void check_number_formatting()
{
  check_number_text(INT64_MIN, "-9223372036854775808");
  check_number_text(INT64_MAX, "9223372036854775807");
  check_number_text(UINT64_MAX, "18446744073709551615");
  check_number_text(SignedBoundary::value, "-9223372036854775808");
  check_number_text(UnsignedBoundary::value, "18446744073709551615");
  volatile int64_t signed_value = INT64_MIN;
  volatile uint64_t unsigned_value = UINT64_MAX;
  check_number_text(signed_value, "-9223372036854775808");
  check_number_text(unsigned_value, "18446744073709551615");
  check_number_text(-1L, "-1");
  check_number_text(17UL, "17");
  char small[4]{};
  int64_t pos = 0;
  assert(databuff_print_key_obj(small, sizeof(small), pos, "value", false, 17L) == OB_SIZE_OVERFLOW);
}

static void check_size_serialization()
{
  char buf[32]{};
  for (size_t value : {size_t(0), size_t(128), SIZE_MAX}) {
    int64_t written = 0;
    assert(serialization::encode(buf, sizeof(buf), written, value) == OB_SUCCESS);
    assert(written == serialization::encoded_length(value));
    size_t result = 0;
    int64_t read = 0;
    assert(serialization::decode(buf, written, read, result) == OB_SUCCESS);
    assert(result == value && read == written);
  }
  if (sizeof(size_t) == 4) {
    for (int64_t invalid : {INT64_C(-1), INT64_C(1) << 32}) {
      int64_t written = 0;
      assert(serialization::encode(buf, sizeof(buf), written, invalid) == OB_SUCCESS);
      size_t result = 17;
      int64_t read = 0;
      assert(serialization::decode(buf, written, read, result) == OB_DESERIALIZE_ERROR);
      assert(result == 17 && read == 0);
    }
  }
}

class StackThreads : public Threads
{
public:
  void run(int64_t) override
  {
    void *base = nullptr;
    assert(get_stackattr(base, stack_size) == OB_SUCCESS);
    result = invoke_large_call();
  }
  __attribute__((noinline)) int invoke_large_call()
  {
    volatile unsigned char frame[64 * 1024];
    for (size_t i = 0; i < sizeof(frame); ++i) { frame[i] = static_cast<unsigned char>(i); }
    int ret = SMART_CALL_LARGE((called = true, OB_SUCCESS));
    for (size_t i = 0; i < sizeof(frame); ++i) { assert(frame[i] == static_cast<unsigned char>(i)); }
    return ret;
  }
  size_t stack_size = 0;
  int result = OB_NOT_INIT;
  bool called = false;
};

static void check_early_thread_stack()
{
  StackThreads workers;
  const int64_t previous = global_thread_stack_size;
  global_thread_stack_size = 1024 * 1024;
  assert(workers.start() == OB_SUCCESS);
  workers.wait();
  workers.destroy();
  global_thread_stack_size = previous;
  fprintf(stderr, "early thread stack=%zu, SMART_CALL_LARGE result=%d, called=%d\n",
          workers.stack_size, workers.result, workers.called);
  assert(workers.result == OB_SUCCESS && workers.called);
}

class AllocatorThreads : public Threads
{
public:
  AllocatorThreads() : Threads(3) {}
  void run(int64_t idx) override
  {
    set_thread_name("WasmAlloc", idx);
    ready.fetch_add(1);
    while (ready.load() != 3) { sched_yield(); }
    for (int i = 0; i < 256; ++i) {
      const int size = 64 + (i % 17) * 4096;
      auto *p = static_cast<unsigned char *>(ob_malloc(size, ObMemAttr("WasmThreads")));
      assert(p != nullptr);
      memset(p, static_cast<int>(idx + 1), size);
      auto *q = static_cast<unsigned char *>(ob_realloc(p, size + 8192, ObMemAttr("WasmThreads")));
      assert(q != nullptr);
      for (int j = 0; j < size; ++j) { assert(q[j] == idx + 1); }
      ob_free(q);
    }
    // Transfer ownership to the main thread after worker TLS teardown.
    transferred[idx] = ob_malloc(32768, ObMemAttr("WasmTransfer"));
    assert(transferred[idx] != nullptr);
    memset(transferred[idx], static_cast<int>(idx + 1), 32768);
    done.fetch_add(1);
    while (!Thread::current().has_set_stop()) { sched_yield(); }
  }
  std::atomic<int> ready{0};
  std::atomic<int> done{0};
  void *transferred[3]{};
};

struct TinyNode {
  TinyNode *next = nullptr;
  uint64_t payload[3]{};
  TinyNode *get_next() const { return next; }
  void set_next(TinyNode *value) { next = value; }
  TinyNode *get_next_atomic() const { return ATOMIC_LOAD(&next); }
  void set_next_atomic(TinyNode *value) { ATOMIC_STORE(&next, value); }
};

static void check_object_pool_alignment()
{
  ObSmallBlockAllocator<> allocator(24, 256, ObMalloc(ObMemAttr("WasmPool")));
  std::vector<uint64_t *> objects;
  for (int i = 0; i < 64; ++i) {
    auto *object = static_cast<uint64_t *>(allocator.alloc(24));
    assert(object != nullptr);
    assert(reinterpret_cast<uintptr_t>(object) % alignof(uint64_t) == 0);
    ATOMIC_STORE(object, static_cast<uint64_t>(i));
    objects.push_back(object);
  }
  for (size_t i = 0; i < objects.size(); ++i) {
    assert(ATOMIC_AAF(objects[i], UINT64_C(1)) == i + 1);
    allocator.free(objects[i]);
  }
  for (int i = 0; i < 64; ++i) {
    auto *object = static_cast<uint64_t *>(allocator.alloc(24));
    assert(object != nullptr);
    assert(reinterpret_cast<uintptr_t>(object) % alignof(uint64_t) == 0);
    allocator.free(object);
  }
}

static void check_tiny_allocator()
{
  ObMemAttr attr("WasmTiny");
  FixedTinyAllocator<TinyNode> allocator(attr);
  std::vector<TinyNode *> nodes;
  std::unordered_set<TinyNode *> unique;
  // More than one block, including the last object in each block.
  while (nodes.size() < 4096) {
    SList<TinyNode> list;
    assert(allocator.alloc(list, 4096 - nodes.size()) == OB_SUCCESS);
    assert(!list.is_empty());
    while (!list.is_empty()) {
      auto *node = list.pop();
      assert(unique.insert(node).second);
      for (auto &word : node->payload) { word = nodes.size(); }
      nodes.push_back(node);
    }
  }
  SList<TinyNode> released;
  for (size_t i = 0; i < nodes.size(); ++i) {
    for (auto word : nodes[i]->payload) { assert(word == i); }
    released.push(nodes[i]);
  }
  allocator.free_slow(released);
  assert(released.is_empty());
}

int main()
{
  AChunkMgr::instance().set_limit(64 * 1024 * 1024);
  AChunkMgr::instance().set_hard_limit(64 * 1024 * 1024);
  AChunkMgr::instance().set_max_chunk_cache_size(0);
  check_early_thread_stack();
  if (sizeof(size_t) == 4) {
    Threads owner;
    Thread oversized(&owner, 0, (INT64_C(1) << 32) + 1048576);
    assert(oversized.start() == OB_ERR_UNEXPECTED);
  }
  check_object_pool_alignment();
  check_tiny_allocator();
  check_number_formatting();
  check_size_serialization();
  {
    hash::ObHashSet<uintptr_t> addresses;
    assert(addresses.create(64) == OB_SUCCESS);
    for (uintptr_t key : {uintptr_t(0), uintptr_t(16), UINTPTR_MAX}) {
      assert(addresses.set_refactored(key) == OB_SUCCESS);
      assert(addresses.exist_refactored(key) == OB_HASH_EXIST);
      assert(addresses.erase_refactored(key) == OB_SUCCESS);
      assert(addresses.exist_refactored(key) == OB_HASH_NOT_EXIST);
    }
    addresses.destroy();
  }
  ObMemAttr attr("WasmRuntime");
  const int sizes[] = {16, 8192, 65536, 2 * 1024 * 1024 + 1};
  for (int size : sizes) {
    auto *memory = static_cast<unsigned char *>(ob_malloc(size, attr));
    assert(memory != nullptr);
    memset(memory, 0x5a, size);
    auto *resized = static_cast<unsigned char *>(ob_realloc(memory, size * 2, attr));
    assert(resized != nullptr);
    for (int i = 0; i < size; ++i) { assert(resized[i] == 0x5a); }
    ob_free(resized);
  }
  for (int round = 0; round < 3; ++round) {
    AllocatorThreads workers;
    assert(workers.start() == OB_SUCCESS);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (workers.done.load() != 3) {
      assert(std::chrono::steady_clock::now() < deadline);
      sched_yield();
    }
    workers.stop();
    workers.wait();
    workers.destroy();
    for (int idx = 0; idx < 3; ++idx) {
      auto *memory = static_cast<unsigned char *>(workers.transferred[idx]);
      for (int j = 0; j < 32768; ++j) { assert(memory[j] == idx + 1); }
      ob_free(memory);
    }
  }
  puts("PASS: seekdb context allocator malloc/realloc/free running in Wasm");
}
