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
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <pthread.h>
#include "lib/lock/ob_latch.h"
#include "lib/utility/ob_platform_utils.h"

using namespace oceanbase::common;

int64_t thread_id_from_peer();

void ob_abort() __THROW
{
  std::abort();
}

struct State {
  ObLatchMutex mutex;
  std::atomic<int> ready{0};
  int counter = 0;
};

struct Worker {
  State *state;
  uintptr_t pointer = 0;
  int64_t id = 0;
  int lock_result = 0;
};

static void *worker(void *arg)
{
  auto &item = *static_cast<Worker *>(arg);
  item.pointer = reinterpret_cast<uintptr_t>(pthread_self());
  item.id = ob_gettid();
  assert(item.id == ob_syscall_gettid());
  assert(item.id == thread_id_from_peer());
  assert(item.id == oceanbase::lib::ob_get_thread_id());
#ifdef __APPLE__
  uint64_t native_id = 0;
  assert(pthread_threadid_np(nullptr, &native_id) == 0);
  assert(item.id == static_cast<int64_t>(native_id));
#endif
  reset_tid_cache();
  assert(ob_gettid() == item.id);
  item.state->ready.fetch_add(1);
  while (item.state->ready.load() != 4) { sched_yield(); }
  item.lock_result = item.state->mutex.lock(ObLatchIds::DEFAULT_MUTEX);
  if (item.lock_result != OB_SUCCESS) { return nullptr; }
  assert(item.state->mutex.get_wid() == item.id);
  ++item.state->counter;
  assert(item.state->mutex.unlock() == OB_SUCCESS);
  for (int i = 0; i < 1000; ++i) {
    assert(item.state->mutex.lock(ObLatchIds::DEFAULT_MUTEX) == OB_SUCCESS);
    assert(item.state->mutex.get_wid() == item.id);
    ++item.state->counter;
    assert(item.state->mutex.unlock() == OB_SUCCESS);
  }
  return nullptr;
}

int main()
{
#ifdef __EMSCRIPTEN__
  static_assert(sizeof(uintptr_t) == 4);
  void *reserved = std::malloc(UINT32_C(1) << 30);
  assert(reserved != nullptr);
  std::printf("reserved=%p\n", reserved);
#endif
  State state;
  Worker workers[4];
  pthread_t threads[4];
  for (int i = 0; i < 4; ++i) {
    workers[i].state = &state;
    assert(pthread_create(&threads[i], nullptr, worker, &workers[i]) == 0);
  }
  for (int i = 0; i < 4; ++i) {
    assert(pthread_join(threads[i], nullptr) == 0);
    std::printf("thread=%d pointer=%llu id=%lld lock_result=%d\n", i,
                static_cast<unsigned long long>(workers[i].pointer),
                static_cast<long long>(workers[i].id), workers[i].lock_result);
    assert(workers[i].lock_result == OB_SUCCESS);
    assert(workers[i].id > 0 && workers[i].id < (INT64_C(1) << 30));
#ifdef __EMSCRIPTEN__
    assert(workers[i].pointer >= (UINT32_C(1) << 30));
    assert(workers[i].pointer % 4 == 0);
#endif
    for (int j = 0; j < i; ++j) { assert(workers[i].id != workers[j].id); }
  }
  assert(state.counter == 4004);
#ifdef __EMSCRIPTEN__
  std::free(reserved);
#endif
  std::puts("seekdb WASM thread ID PASS");
}
