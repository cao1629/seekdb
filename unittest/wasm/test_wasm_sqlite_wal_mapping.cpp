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
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <malloc.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <emscripten/heap.h>
#include "sqlite3.h"

using MmapFunction = void *(*)(void *, size_t, int, int, int, off_t);
using MunmapFunction = int (*)(void *, size_t);
static MmapFunction real_mmap;
static MunmapFunction real_munmap;
static std::atomic<uint64_t> map_calls{0};
static std::atomic<uint64_t> map_bytes{0};
static std::atomic<uint64_t> unmap_calls{0};
static std::atomic<uint64_t> unmap_bytes{0};
static std::atomic<uint64_t> unmap_failures{0};
static std::atomic<uint64_t> unmap_failure_bytes{0};
static std::atomic<int> unmap_error{0};

void *record_mmap(void *address, size_t length, int protection, int flags, int fd, off_t offset)
{
  void *result = real_mmap(address, length, protection, flags, fd, offset);
  const int error = errno;
  if (result != MAP_FAILED) {
    assert(length == 65536);
    map_calls.fetch_add(1, std::memory_order_relaxed);
    map_bytes.fetch_add(length, std::memory_order_relaxed);
  }
  errno = error;
  return result;
}

int record_munmap(void *address, size_t length)
{
  const int result = real_munmap(address, length);
  const int error = errno;
  unmap_calls.fetch_add(1, std::memory_order_relaxed);
  if (result == 0) {
    unmap_bytes.fetch_add(length, std::memory_order_relaxed);
  } else {
    unmap_failures.fetch_add(1, std::memory_order_relaxed);
    unmap_failure_bytes.store(length, std::memory_order_relaxed);
    unmap_error.store(error, std::memory_order_relaxed);
  }
  errno = error;
  return result;
}

void execute(sqlite3 *connection, const char *sql)
{
  char *error = nullptr;
  const int result = sqlite3_exec(connection, sql, nullptr, nullptr, &error);
  if (result != SQLITE_OK) std::fprintf(stderr, "SQL error %d: %s; %s\n", result, sql, error ? error : "");
  sqlite3_free(error);
  assert(result == SQLITE_OK);
}

int read_value(sqlite3 *connection)
{
  sqlite3_stmt *statement = nullptr;
  assert(sqlite3_prepare_v2(connection, "SELECT value FROM values_table WHERE id=1", -1, &statement, nullptr) == SQLITE_OK);
  assert(sqlite3_step(statement) == SQLITE_ROW);
  const int value = sqlite3_column_int(statement, 0);
  assert(sqlite3_step(statement) == SQLITE_DONE);
  assert(sqlite3_finalize(statement) == SQLITE_OK);
  return value;
}

sqlite3 *open_connection()
{
  sqlite3 *connection = nullptr;
  assert(sqlite3_open_v2("/wal-probe/data.db", &connection,
      SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nullptr) == SQLITE_OK);
  assert(sqlite3_busy_timeout(connection, 1000) == SQLITE_OK);
  execute(connection, "PRAGMA mmap_size=0");
  return connection;
}

void *read_and_close(void *argument)
{
  auto *connection = static_cast<sqlite3 *>(argument);
  assert(read_value(connection) == 256);
  assert(sqlite3_close(connection) == SQLITE_OK);
  return nullptr;
}

void *read_reopen(void *)
{
  for (int i = 0; i < 32; ++i) {
    sqlite3 *connection = open_connection();
    assert(read_value(connection) == 256);
    assert(sqlite3_close(connection) == SQLITE_OK);
  }
  return nullptr;
}

void check_shared_connections()
{
  const uint64_t maps_before = map_calls.load();
  const uint64_t unmaps_before = unmap_calls.load();
  sqlite3 *first = open_connection();
  assert(read_value(first) == 256);
  sqlite3 *second = open_connection();
  assert(read_value(second) == 256);
  assert(map_calls.load() == maps_before + 1);
  assert(sqlite3_close(first) == SQLITE_OK);
  assert(unmap_calls.load() == unmaps_before);
  assert(read_value(second) == 256);
  assert(sqlite3_close(second) == SQLITE_OK);
  assert(unmap_calls.load() == unmaps_before + 1);
}

void check_multiple_mappings()
{
  const uint64_t maps_before = map_calls.load();
  const uint64_t unmaps_before = unmap_calls.load();
  sqlite3 *connection = open_connection();
  assert(read_value(connection) == 256);
  sqlite3_file *file = nullptr;
  assert(sqlite3_file_control(connection, "main", SQLITE_FCNTL_FILE_POINTER, &file) == SQLITE_OK);
  assert(file != nullptr && file->pMethods->iVersion >= 2);
  void volatile *second_mapping = nullptr;
  void volatile *second_region = nullptr;
  assert(file->pMethods->xShmMap(file, 2, 32768, 1, &second_mapping) == SQLITE_OK);
  assert(file->pMethods->xShmMap(file, 3, 32768, 1, &second_region) == SQLITE_OK);
  assert(second_mapping != nullptr && second_region != nullptr);
  assert(reinterpret_cast<uintptr_t>(second_region) - reinterpret_cast<uintptr_t>(second_mapping) == 32768);
  assert(map_calls.load() == maps_before + 2);
  assert(read_value(connection) == 256);
  assert(sqlite3_close(connection) == SQLITE_OK);
  assert(unmap_calls.load() == unmaps_before + 2);
}

int main(int argc, char **argv)
{
  std::setvbuf(stdout, nullptr, _IONBF, 0);
  const bool expect_leak = argc == 2 && std::strcmp(argv[1], "--expect-leak") == 0;
  assert(emscripten_get_heap_size() == 64 * 1024 * 1024);
  assert(sysconf(_SC_PAGESIZE) == 65536);
  assert(sqlite3_initialize() == SQLITE_OK);
  sqlite3_vfs *vfs = sqlite3_vfs_find(nullptr);
  assert(vfs != nullptr && vfs->iVersion >= 3);
  real_mmap = reinterpret_cast<MmapFunction>(vfs->xGetSystemCall(vfs, "mmap"));
  real_munmap = reinterpret_cast<MunmapFunction>(vfs->xGetSystemCall(vfs, "munmap"));
  assert(real_mmap != nullptr && real_munmap != nullptr);
  assert(vfs->xSetSystemCall(vfs, "mmap", reinterpret_cast<sqlite3_syscall_ptr>(record_mmap)) == SQLITE_OK);
  assert(vfs->xSetSystemCall(vfs, "munmap", reinterpret_cast<sqlite3_syscall_ptr>(record_munmap)) == SQLITE_OK);
  assert(mkdir("/wal-probe", 0700) == 0);
  sqlite3 *connection = open_connection();
  execute(connection, "PRAGMA journal_mode=WAL; CREATE TABLE values_table(id INTEGER PRIMARY KEY,value INTEGER); INSERT INTO values_table VALUES(1,0)");
  assert(read_value(connection) == 0);
  assert(sqlite3_close(connection) == SQLITE_OK);
  const size_t used_before = mallinfo().uordblks;
  const uint64_t maps_before = map_calls.load();
  for (int i = 1; i <= 256; ++i) {
    connection = open_connection();
    execute(connection, "BEGIN; UPDATE values_table SET value=value+1 WHERE id=1; COMMIT");
    assert(read_value(connection) == i);
    assert(sqlite3_close(connection) == SQLITE_OK);
  }
  const size_t used_after_cycles = mallinfo().uordblks;
  const uint64_t maps_after_cycles = map_calls.load();
  check_shared_connections();
  check_multiple_mappings();
  connection = open_connection();
  assert(read_value(connection) == 256);
  pthread_t worker;
  assert(pthread_create(&worker, nullptr, read_and_close, connection) == 0);
  assert(pthread_join(worker, nullptr) == 0);
  pthread_t readers[2];
  for (auto &reader : readers) assert(pthread_create(&reader, nullptr, read_reopen, nullptr) == 0);
  for (auto &reader : readers) assert(pthread_join(reader, nullptr) == 0);
  connection = open_connection();
  assert(read_value(connection) == 256);
  sqlite3_stmt *integrity = nullptr;
  assert(sqlite3_prepare_v2(connection, "PRAGMA integrity_check", -1, &integrity, nullptr) == SQLITE_OK);
  assert(sqlite3_step(integrity) == SQLITE_ROW);
  assert(std::strcmp(reinterpret_cast<const char *>(sqlite3_column_text(integrity, 0)), "ok") == 0);
  assert(sqlite3_step(integrity) == SQLITE_DONE);
  assert(sqlite3_finalize(integrity) == SQLITE_OK);
  assert(sqlite3_close(connection) == SQLITE_OK);
  const auto memory = mallinfo();
  std::printf("{\"expect_leak\":%s,\"sqlite_version\":\"%s\",\"page_size\":%ld,\"heap_bytes\":%zu,\"cycles\":256,\"maps_during_cycles\":%llu,\"used_before\":%zu,\"used_after_cycles\":%zu,\"cycle_used_delta\":%lld,\"map_calls\":%llu,\"map_bytes\":%llu,\"unmap_calls\":%llu,\"unmap_success_bytes\":%llu,\"unmap_failures\":%llu,\"unmap_failure_length\":%llu,\"unmap_errno\":%d,\"EINVAL\":%d,\"final_sdk_used\":%zu,\"final_sqlite_used\":%lld,\"cross_thread_read_close\":true,\"concurrent_read_reopen\":true}\n",
      expect_leak ? "true" : "false", sqlite3_libversion(), sysconf(_SC_PAGESIZE),
      emscripten_get_heap_size(), maps_after_cycles - maps_before, used_before, used_after_cycles,
      static_cast<long long>(used_after_cycles) - used_before, map_calls.load(), map_bytes.load(),
      unmap_calls.load(), unmap_bytes.load(), unmap_failures.load(), unmap_failure_bytes.load(),
      unmap_error.load(), EINVAL, memory.uordblks, sqlite3_memory_used());
  assert(map_calls.load() >= 258 && maps_after_cycles - maps_before == 256);
  assert(map_calls.load() == unmap_calls.load());
  if (expect_leak) {
    assert(unmap_failures.load() == map_calls.load());
    assert(unmap_error.load() == EINVAL && unmap_failure_bytes.load() == 32768);
    assert(used_after_cycles - used_before >= 256 * 65536);
  } else {
    assert(unmap_failures.load() == 0);
    assert(map_bytes.load() == unmap_bytes.load());
    assert(used_after_cycles <= used_before + 65536);
  }
  assert(memory.arena + memory.hblkhd == memory.uordblks + memory.fordblks);
  assert(sqlite3_shutdown() == SQLITE_OK);
  std::puts("PASS: WAL mapping accounting and SQL reopen checks");
}
