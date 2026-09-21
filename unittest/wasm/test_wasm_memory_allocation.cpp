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

#include "lib/resource/wasm_memory.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <malloc.h>

using oceanbase::lib::allocate_wasm_memory;
using oceanbase::lib::free_wasm_memory;

constexpr size_t alignment = 2U * 1024U * 1024U;
constexpr size_t payload_size = alignment;
void *occupied[65536];
size_t occupied_count = 0;

void occupy_available_memory()
{
  for (size_t amount : {size_t(65536), size_t(64), size_t(1)}) {
    for (;;) {
      void *ptr = emscripten_builtin_malloc(amount);
      if (ptr == nullptr) {
        break;
      }
      assert(occupied_count < sizeof(occupied) / sizeof(occupied[0]));
      occupied[occupied_count++] = ptr;
    }
  }
}

void release_occupied_memory()
{
  while (occupied_count > 0) {
    emscripten_builtin_free(occupied[--occupied_count]);
  }
}

void assert_zeroed(const void *ptr, size_t size)
{
  volatile const uint8_t *bytes = static_cast<const uint8_t *>(ptr);
  for (size_t i = 0; i < size; ++i) {
    assert(bytes[i] == 0);
  }
}

void test_validation()
{
  errno = 0;
  assert(allocate_wasm_memory(0, alignment) == nullptr && errno == EINVAL);
  errno = 0;
  assert(allocate_wasm_memory(1, 0) == nullptr && errno == EINVAL);
  errno = 0;
  assert(allocate_wasm_memory(1, sizeof(void *) / 2) == nullptr && errno == EINVAL);
  errno = 0;
  assert(allocate_wasm_memory(1, 3 * sizeof(void *)) == nullptr && errno == EINVAL);
  errno = 0;
  assert(allocate_wasm_memory(uint64_t(SIZE_MAX) - alignment + 1, alignment) == nullptr);
  assert(errno == ENOMEM);
  errno = 0;
  assert(allocate_wasm_memory(uint64_t(SIZE_MAX) + 1, alignment) == nullptr && errno == ENOMEM);
  void *ptr = allocate_wasm_memory(67, 8);
  assert(ptr != nullptr && (reinterpret_cast<uintptr_t>(ptr) & 7) == 0);
  assert_zeroed(ptr, 67);
  free_wasm_memory(ptr);
}

void test_aligned_hole()
{
  void *hole = emscripten_builtin_memalign(alignment, payload_size);
  assert(hole != nullptr && (reinterpret_cast<uintptr_t>(hole) & (alignment - 1)) == 0);
  std::memset(hole, 0xa5, payload_size);
  occupy_available_memory();
  emscripten_builtin_free(hole);
  const auto info = mallinfo();
  assert(emscripten_builtin_memalign(alignment, payload_size) == nullptr);
  void *plain = emscripten_builtin_malloc(payload_size);
  assert(plain == hole);
  emscripten_builtin_free(plain);
  void *ptr = allocate_wasm_memory(payload_size, alignment);
  assert(ptr == hole);
  assert_zeroed(ptr, payload_size);
  free_wasm_memory(ptr);
  release_occupied_memory();
  std::printf("PASS: aligned hole reused and zeroed (address=%u, free=%zu, top=%zu)\n",
      static_cast<unsigned>(reinterpret_cast<uintptr_t>(hole)), info.fordblks, info.keepcost);
}

void test_unaligned_hole()
{
  void *prefix = emscripten_builtin_malloc(64);
  assert(prefix != nullptr);
  void *hole = emscripten_builtin_malloc(payload_size);
  assert(hole != nullptr && (reinterpret_cast<uintptr_t>(hole) & (alignment - 1)) != 0);
  occupy_available_memory();
  emscripten_builtin_free(hole);
  const auto info = mallinfo();
  assert(emscripten_builtin_memalign(alignment, payload_size) == nullptr);
  errno = 0;
  assert(allocate_wasm_memory(payload_size, alignment) == nullptr && errno == ENOMEM);
  void *recovered = emscripten_builtin_malloc(payload_size);
  assert(recovered == hole);
  emscripten_builtin_free(recovered);
  release_occupied_memory();
  emscripten_builtin_free(prefix);
  std::printf("PASS: unaligned candidate rejected and released (address=%u, free=%zu)\n",
      static_cast<unsigned>(reinterpret_cast<uintptr_t>(hole)), info.fordblks);
}

int main()
{
  std::setbuf(stdout, nullptr);
  assert(emscripten_get_heap_size() == 64U * 1024U * 1024U);
  test_validation();
  test_aligned_hole();
  test_unaligned_hole();
  std::puts("PASS: production WASM allocation validation and constrained-hole fallback");
}
