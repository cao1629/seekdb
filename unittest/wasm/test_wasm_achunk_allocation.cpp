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
#include <cassert>
#include <cerrno>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include "lib/resource/achunk_mgr.h"
#include "lib/allocator/ob_malloc.h"

using namespace oceanbase::lib;
using namespace oceanbase::common;

static constexpr uint64_t mib = 1024 * 1024;
static size_t alloc_size = 0;
static size_t alloc_calls = 0;
static void *tracked_ptr = nullptr;
static bool tracked_freed = false;

extern "C" void *__real_emscripten_builtin_memalign(size_t, size_t);
extern "C" void __real_emscripten_builtin_free(void *);

extern "C" void *__wrap_emscripten_builtin_memalign(size_t alignment, size_t size)
{
  void *result = __real_emscripten_builtin_memalign(alignment, size);
  if (alignment == INTACT_ACHUNK_SIZE) {
    alloc_size = size;
    ++alloc_calls;
  }
  return result;
}

extern "C" void __wrap_emscripten_builtin_free(void *ptr)
{
  if (ptr != nullptr && ptr == tracked_ptr) { tracked_freed = true; }
  __real_emscripten_builtin_free(ptr);
}

static uint64_t expected_size(uint64_t payload)
{
  const uint64_t page = 65536;
  uint64_t bytes = ((payload + ACHUNK_HEADER_SIZE + page - 1) / page) * page;
  return bytes < INTACT_ACHUNK_SIZE ? INTACT_ACHUNK_SIZE : bytes;
}

static void check_totals(AChunkMgr &mgr, int64_t hold, int64_t cached)
{
  assert(mgr.get_hold() == hold);
  assert(mgr.get_total_hold() == hold);
  assert(mgr.get_freelist_hold() == cached);
  assert(mgr.get_used() == hold - cached);
}

static void check_chunk(AChunk *chunk, uint64_t payload)
{
  assert(chunk != nullptr && chunk->is_valid());
  assert(reinterpret_cast<uintptr_t>(chunk) % INTACT_ACHUNK_SIZE == 0);
  const uint64_t physical = expected_size(payload);
  uint64_t available = 0;
  assert(chunk->alloc_bytes_ == payload);
  assert(chunk->aligned() == physical && chunk->hold(&available) == physical);
  assert(available == physical - ACHUNK_HEADER_SIZE);
  assert(AChunk::calc_hold(payload, 65536) == physical);
  assert(AChunk::ptr2chunk(reinterpret_cast<char *>(chunk) + ACHUNK_HEADER_SIZE) == chunk);
  if (payload != 0) {
    auto *data = reinterpret_cast<unsigned char *>(chunk) + ACHUNK_HEADER_SIZE;
    assert(data[0] == 0 && data[payload - 1] == 0);
    memset(data, 0x6d, payload);
    assert(data[0] == 0x6d && data[payload - 1] == 0x6d);
  }
}

static void check_boundaries()
{
  AChunkMgr mgr;
  mgr.set_limit(64 * mib);
  mgr.set_hard_limit(64 * mib);
  mgr.set_max_chunk_cache_size(0);
  const uint64_t normal_payload = INTACT_ACHUNK_SIZE - ACHUNK_HEADER_SIZE;
  const uint64_t values[] = {0, 1, normal_payload - 1, normal_payload,
    normal_payload + 1, 2 * mib, 2 * mib + 65536 - ACHUNK_HEADER_SIZE,
    2 * mib + 65536 - ACHUNK_HEADER_SIZE + 1, 3 * mib,
    4 * mib - ACHUNK_HEADER_SIZE, 4 * mib - ACHUNK_HEADER_SIZE + 1,
    20 * mib, 20 * mib + 1};
  for (uint64_t payload : values) {
    alloc_calls = 0;
    AChunk *chunk = mgr.alloc_chunk(payload);
    assert(alloc_calls == 1);
    printf("allocation payload=%" PRIu64 " requested=%zu expected=%" PRIu64 "\n",
           payload, alloc_size, expected_size(payload));
    assert(alloc_size == expected_size(payload));
    check_chunk(chunk, payload);
    check_totals(mgr, expected_size(payload), 0);
    tracked_ptr = chunk;
    tracked_freed = false;
    mgr.free_chunk(chunk);
    assert(tracked_freed);
    tracked_ptr = nullptr;
    check_totals(mgr, 0, 0);
  }
}

static void check_cache()
{
  AChunkMgr mgr;
  mgr.set_limit(64 * mib);
  mgr.set_hard_limit(64 * mib);
  mgr.set_max_chunk_cache_size(32 * mib, true);
  AChunk *normal = mgr.alloc_chunk(INTACT_ACHUNK_SIZE - ACHUNK_HEADER_SIZE);
  assert(normal != nullptr);
  tracked_ptr = normal;
  tracked_freed = false;
  mgr.free_chunk(normal);
  assert(!tracked_freed);
  check_totals(mgr, 2 * mib, 2 * mib);
  alloc_calls = 0;
  AChunk *reused = mgr.alloc_chunk(1024);
  assert(reused == normal && alloc_calls == 0);
  check_totals(mgr, 2 * mib, 0);
  mgr.free_chunk(reused);
  assert(mgr.sync_wash() == 2 * mib);
  assert(tracked_freed);
  tracked_ptr = nullptr;
  check_totals(mgr, 0, 0);

  AChunk *small = mgr.alloc_chunk(2 * mib);
  check_chunk(small, 2 * mib);
  tracked_ptr = small;
  tracked_freed = false;
  mgr.free_chunk(small);
  assert(tracked_freed);
  check_totals(mgr, 0, 0);
  tracked_ptr = nullptr;
  alloc_calls = 0;
  AChunk *larger = mgr.alloc_chunk(3 * mib);
  assert(larger != nullptr);
  assert(alloc_calls == 1);
  assert(larger->aligned() == expected_size(3 * mib));
  auto *data = reinterpret_cast<unsigned char *>(larger) + ACHUNK_HEADER_SIZE;
  memset(data, 0x72, 3 * mib);
  assert(data[3 * mib - 1] == 0x72);
  check_totals(mgr, expected_size(3 * mib), 0);
  mgr.free_chunk(larger);
  assert(mgr.sync_wash() == 0);
  check_totals(mgr, 0, 0);
  puts("PASS: normal cache reuse and explicit large-cache preference");
}

static void check_coroutine_and_failures()
{
  AChunkMgr mgr;
  mgr.set_limit(64 * mib);
  mgr.set_hard_limit(64 * mib);
  mgr.set_max_chunk_cache_size(32 * mib, true);
  for (uint64_t payload : {UINT64_C(1024), 2 * mib, 3 * mib + 1}) {
    alloc_calls = 0;
    AChunk *chunk = mgr.alloc_co_chunk(payload);
    assert(alloc_calls == 1 && alloc_size == expected_size(payload));
    check_chunk(chunk, payload);
    check_totals(mgr, expected_size(payload), 0);
    tracked_ptr = chunk;
    tracked_freed = false;
    mgr.free_co_chunk(chunk);
    assert(tracked_freed);
    tracked_ptr = nullptr;
    check_totals(mgr, 0, 0);
  }
  mgr.set_limit(0);
  mgr.set_hard_limit(0);
  assert(mgr.alloc_chunk(2 * mib) == nullptr);
  check_totals(mgr, 0, 0);
  mgr.set_limit(1024 * mib);
  mgr.set_hard_limit(1024 * mib);
  assert(mgr.alloc_chunk(160 * mib) == nullptr);
  check_totals(mgr, 0, 0);
  assert(mgr.alloc_co_chunk(160 * mib) == nullptr);
  check_totals(mgr, 0, 0);
  assert(mgr.alloc_chunk(UINT32_MAX, true) == nullptr);
  check_totals(mgr, 0, 0);
  assert(mgr.alloc_co_chunk(UINT32_MAX) == nullptr);
  check_totals(mgr, 0, 0);
  AChunk *chunk = mgr.alloc_chunk(2 * mib);
  assert(chunk != nullptr);
  errno = 0;
  assert(mgr.madvise(chunk, 65536, MADV_DONTNEED) == -1 && errno == ENOTSUP);
  check_totals(mgr, expected_size(2 * mib), 0);
  mgr.set_max_chunk_cache_size(0);
  mgr.free_chunk(chunk);
  check_totals(mgr, 0, 0);
  puts("PASS: coroutine free, budget and physical allocation rollback, no false decommit");
}

static void check_live_allocations()
{
  AChunkMgr mgr;
  mgr.set_limit(64 * mib);
  mgr.set_hard_limit(64 * mib);
  mgr.set_max_chunk_cache_size(32 * mib, true);
  AChunk *normal = mgr.alloc_chunk(1024);
  AChunk *large = mgr.alloc_chunk(3 * mib);
  AChunk *co = mgr.alloc_co_chunk(2 * mib);
  assert(normal != nullptr && large != nullptr && co != nullptr);
  const uint64_t live = 2 * mib + expected_size(3 * mib) + expected_size(2 * mib);
  check_totals(mgr, live, 0);
  assert(mgr.alloc_chunk(160 * mib, true) == nullptr);
  check_totals(mgr, live, 0);
  mgr.free_chunk(normal);
  check_totals(mgr, live, 2 * mib);
  mgr.free_co_chunk(co);
  check_totals(mgr, live - expected_size(2 * mib), 2 * mib);
  mgr.free_chunk(large);
  const uint64_t cached = 2 * mib;
  check_totals(mgr, cached, cached);
  assert(mgr.sync_wash() == cached);
  check_totals(mgr, 0, 0);

  mgr.set_limit(32 * mib);
  mgr.set_hard_limit(32 * mib);
  mgr.set_max_chunk_cache_size(0);
  AChunk *chunks[32]{};
  size_t count = 0;
  while (count < 32 && (chunks[count] = mgr.alloc_chunk(2 * mib)) != nullptr) { ++count; }
  assert(count == (32 * mib) / expected_size(2 * mib));
  check_totals(mgr, count * expected_size(2 * mib), 0);
  printf("capacity budget=%" PRIu64 " payload=%" PRIu64 " count=%zu hold=%" PRId64 "\n",
         32 * mib, 2 * mib, count, mgr.get_hold());
  for (size_t i = 0; i < count; ++i) { mgr.free_chunk(chunks[i]); }
  check_totals(mgr, 0, 0);
  puts("PASS: mixed live allocation accounting, failed allocation preserves live hold, bounded capacity");
}

static void check_context_allocator()
{
  AChunkMgr::instance().set_limit(64 * mib);
  AChunkMgr::instance().set_hard_limit(64 * mib);
  AChunkMgr::instance().set_max_chunk_cache_size(0);
  ObMemAttr attr("LargeChunkProbe");
  for (uint64_t size : {UINT64_C(1024), 2 * mib - 16384, 2 * mib, 3 * mib + 7, 4 * mib}) {
    auto *ptr = static_cast<unsigned char *>(ob_malloc(size, attr));
    assert(ptr != nullptr);
    memset(ptr, 0x39, size);
    auto *resized = static_cast<unsigned char *>(ob_realloc(ptr, size + 65536, attr));
    assert(resized != nullptr);
    for (uint64_t i = 0; i < size; ++i) { assert(resized[i] == 0x39); }
    memset(resized + size, 0x61, 65536);
    assert(resized[size + 65535] == 0x61);
    ob_free(resized);
  }
  puts("PASS: real context allocator large malloc/realloc/free metadata and payload");
}

int main()
{
  setvbuf(stdout, nullptr, _IONBF, 0);
  assert(get_page_size() == 65536);
  assert(ACHUNK_HEADER_SIZE == 16384);
  check_boundaries();
  check_cache();
  check_coroutine_and_failures();
  check_live_allocations();
  check_context_allocator();
  puts("PASS: WASM AChunk allocation sizes, cache reuse, accounting and context allocator");
}
