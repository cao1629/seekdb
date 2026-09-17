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
#include <chrono>
#include <cstdio>
#include <cstring>
#include <sstream>
#include <thread>
#include <unistd.h>
#define private public
#define protected public
#include "storage/meta_mem/ob_storage_meta_mem_mgr.h"
#include "storage/tx_storage/ob_ls_service.h"
#undef protected
#undef private
#include "share/rc/ob_server_runtime.h"

using namespace oceanbase;
using namespace oceanbase::common;
using namespace oceanbase::storage;

static std::atomic<int> freed_ls{0};
static ObStorageMetaMemMgr *test_manager;

extern "C" void __wrap__ZN9oceanbase7storage11ObLSService8free_ls_EPNS0_4ObLSE(
    ObLSService *service, ObLS *ls)
{
  bool released = false;
  assert(test_manager->check_all_meta_mem_released(released, "test_free_ls") == OB_SUCCESS);
  assert(released);
  assert(service->ls_ == ls && ls != nullptr);
  service->ls_ = nullptr;
  freed_ls.fetch_add(1);
}

static ObTablet *enqueue_tablet(ObStorageMetaMemMgr &manager)
{
  ObTablet *tablet = nullptr;
  assert(manager.acquire_tablet(&manager.tablet_buffer_pool_, tablet) == OB_SUCCESS);
  assert(tablet != nullptr && tablet->get_ref() == 0);
  assert(manager.inner_push_tablet_into_gc_queue(tablet) == OB_SUCCESS);
  return tablet;
}

static void enqueue_tablets(ObStorageMetaMemMgr &manager, int count)
{
  for (int i = 0; i < count; ++i) enqueue_tablet(manager);
  assert(manager.tablet_buffer_pool_.get_used_obj_cnt() == manager.tablet_gc_queue_.count());
}

static void check_normal_batch_and_requeue(ObStorageMetaMemMgr &manager)
{
  enqueue_tablets(manager, 601);
  bool cleaned = false;
  assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
  assert(!cleaned && manager.tablet_gc_queue_.count() == 401);
  assert(manager.tablet_buffer_pool_.get_used_obj_cnt() == 401);
  while (!cleaned) assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
  ObTablet *retained = enqueue_tablet(manager);
  retained->inc_ref();
  assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
  assert(!cleaned && manager.tablet_gc_queue_.count() == 1);
  assert(manager.tablet_buffer_pool_.get_used_obj_cnt() == 1);
  assert(retained->dec_ref() == 0);
  assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
  assert(cleaned && manager.tablet_buffer_pool_.get_used_obj_cnt() == 0);
}

static void check_wait(ObStorageMetaMemMgr &manager, bool baseline, bool concurrent,
                       bool held_reference)
{
  ObLSService service;
  alignas(8) char ownership_token[8]{};
  service.ls_ = reinterpret_cast<ObLS *>(ownership_token);
  enqueue_tablets(manager, 600);
  ObTablet *held = enqueue_tablet(manager);
  if (held_reference) held->inc_ref();
  std::atomic<bool> done{false};
  std::atomic<bool> consumer_stop{false};
  const int previous_frees = freed_ls.load();
  const auto before = std::chrono::steady_clock::now();
  std::thread waiter([&] {
    assert(service.wait() == OB_SUCCESS);
    done.store(true);
  });
  std::thread consumer;
  if (concurrent) {
    consumer = std::thread([&] {
      while (!consumer_stop.load()) {
        bool cleaned = false;
        assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
        usleep(1000);
      }
    });
  }
  if (held_reference) {
    usleep(100000);
    assert(!done.load() && freed_ls.load() == previous_frees);
    assert(held->get_ref() == 1);
    assert(manager.tablet_buffer_pool_.get_used_obj_cnt() >= 1);
    assert(held->dec_ref() == 0);
  }
  if (baseline && !concurrent) {
    usleep(100000);
    assert(!done.load());
    assert(freed_ls.load() == previous_frees);
    assert(manager.tablet_gc_queue_.count() == 601);
    bool cleaned = false;
    while (!cleaned) assert(manager.gc_tablets_in_queue(cleaned) == OB_SUCCESS);
  }
  for (int i = 0; i < 2000 && !done.load(); ++i) usleep(1000);
  assert(done.load());
  waiter.join();
  consumer_stop.store(true);
  if (consumer.joinable()) consumer.join();
  assert(service.ls_ == nullptr && freed_ls.load() == previous_frees + 1);
  assert(manager.tablet_gc_queue_.count() == 0);
  assert(manager.tablet_buffer_pool_.get_used_obj_cnt() == 0);
  const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now() - before).count();
  std::printf("wait baseline=%d concurrent=%d held_reference=%d elapsed_us=%lld freed_ls=%d\n",
      baseline, concurrent, held_reference, static_cast<long long>(elapsed), freed_ls.load());
}

int main(int argc, char **argv)
{
  const bool baseline = argc > 1 && std::strcmp(argv[1], "baseline") == 0;
  OB_LOGGER.set_log_level("ERROR");
  ObStorageMetaMemMgr manager;
  assert(manager.init() == OB_SUCCESS);
  test_manager = &manager;
  share::bind_server_service(&manager);
  check_normal_batch_and_requeue(manager);
  check_wait(manager, baseline, false, false);
  check_wait(manager, baseline, true, false);
  check_wait(manager, baseline, false, true);
  share::unbind_server_service<ObStorageMetaMemMgr>();
  test_manager = nullptr;
  std::puts("PASS: actual tablet pool GC, bounded normal batch, reference-error requeue, LS wait release gate, concurrent consumer");
}
