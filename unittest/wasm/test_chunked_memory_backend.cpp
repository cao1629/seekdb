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

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <vector>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <emscripten/heap.h>
#include <emscripten/wasmfs.h>

extern "C" backend_t seekdb_create_memory_backend();
extern "C" void *__real_calloc(size_t, size_t);
extern "C" void *__real_realloc(void *, size_t);

namespace {

constexpr size_t CHUNK_SIZE = 64 * 1024;
std::atomic<int> calloc_remaining{-1};
std::atomic<int> realloc_remaining{-1};
std::atomic<int> allocation_failures{0};
std::atomic<int> worker_request{0};
std::atomic<int> worker_completed{0};
int worker_fd = -1;
int worker_result = -1;
off_t worker_size = -1;

bool shouldFail(std::atomic<int> &remaining)
{
  const int value = remaining.load();
  if (value < 0) {
    return false;
  }
  if (value == 0) {
    ++allocation_failures;
    errno = ENOMEM;
    return true;
  }
  --remaining;
  return false;
}

double now()
{
  timespec ts;
  assert(clock_gettime(CLOCK_MONOTONIC, &ts) == 0);
  return ts.tv_sec + ts.tv_nsec / 1e9;
}

void *statWorker(void *)
{
  int completed = 0;
  for (;;) {
    const int request = worker_request.load();
    if (request < 0) {
      return nullptr;
    }
    if (request != completed) {
      struct stat st;
      worker_result = fstat(worker_fd, &st);
      worker_size = worker_result == 0 ? st.st_size : -1;
      completed = request;
      worker_completed.store(completed);
    }
    sched_yield();
  }
}

void checkWorkerStat(int fd, off_t size)
{
  worker_fd = fd;
  const int request = worker_request.load() + 1;
  worker_request.store(request);
  const double deadline = now() + 5;
  while (worker_completed.load() != request && now() < deadline) {
    sched_yield();
  }
  assert(worker_completed.load() == request);
  assert(worker_result == 0);
  assert(worker_size == size);
}

int openFile(const char *path)
{
  const int fd = open(path, O_CREAT | O_RDWR | O_TRUNC, 0600);
  assert(fd >= 0);
  return fd;
}

void checkZeros(int fd, off_t offset, size_t size)
{
  std::vector<uint8_t> bytes(size, 0xa5);
  assert(pread(fd, bytes.data(), bytes.size(), offset) == static_cast<ssize_t>(bytes.size()));
  assert(std::all_of(bytes.begin(), bytes.end(), [](uint8_t value) { return value == 0; }));
}

void testGrowthAndTruncate()
{
  const int fd = openFile("/chunked/growth");
  std::vector<uint8_t> expected(3 * 1024 * 1024 + 79);
  for (size_t i = 0; i < expected.size(); ++i) {
    expected[i] = static_cast<uint8_t>((i * 37 + i / CHUNK_SIZE) % 251 + 1);
  }
  size_t written = 0;
  while (written < expected.size()) {
    const size_t count = std::min(expected.size() - written, CHUNK_SIZE + 113);
    assert(pwrite(fd, expected.data() + written, count, written) == static_cast<ssize_t>(count));
    written += count;
  }
  std::vector<uint8_t> actual(expected.size());
  assert(pread(fd, actual.data(), actual.size(), 0) == static_cast<ssize_t>(actual.size()));
  assert(actual == expected);
  const off_t retained = CHUNK_SIZE + 31;
  assert(ftruncate(fd, retained) == 0);
  assert(ftruncate(fd, expected.size()) == 0);
  checkZeros(fd, retained, expected.size() - retained);
  actual.resize(retained);
  assert(pread(fd, actual.data(), actual.size(), 0) == static_cast<ssize_t>(actual.size()));
  assert(std::equal(actual.begin(), actual.end(), expected.begin()));
  assert(ftruncate(fd, 0) == 0);
  assert(ftruncate(fd, CHUNK_SIZE * 2) == 0);
  checkZeros(fd, 0, CHUNK_SIZE * 2);
  assert(close(fd) == 0);
}

void testSparseAndLargeOffsets()
{
  const int fd = openFile("/chunked/sparse");
  const int failures = allocation_failures.load();
  calloc_remaining.store(0);
  realloc_remaining.store(0);
  assert(ftruncate(fd, off_t{1} << 39) == 0);
  assert(posix_fallocate(fd, off_t{1} << 39, CHUNK_SIZE) == 0);
  assert(allocation_failures.load() == failures);
  calloc_remaining.store(-1);
  realloc_remaining.store(-1);
  checkWorkerStat(fd, (off_t{1} << 39) + CHUNK_SIZE);
  checkZeros(fd, (off_t{1} << 39) - 17, 47);
  const off_t offset = (off_t{1} << 32) + 123;
  const uint8_t marker[] = {19, 43, 71};
  assert(pwrite(fd, marker, sizeof(marker), offset) == sizeof(marker));
  uint8_t readback[5] = {};
  assert(pread(fd, readback, sizeof(readback), offset - 1) == sizeof(readback));
  assert(readback[0] == 0 && readback[4] == 0);
  assert(std::memcmp(readback + 1, marker, sizeof(marker)) == 0);
  assert(ftruncate(fd, offset + 1) == 0);
  assert(ftruncate(fd, offset + 3) == 0);
  checkZeros(fd, offset + 1, 2);
  errno = 0;
  assert(ftruncate(fd, std::numeric_limits<off_t>::max()) == -1 && errno == EFBIG);
  errno = 0;
  assert(pwrite(fd, marker, sizeof(marker), std::numeric_limits<off_t>::max() - 1) == -1);
  assert(errno == EFBIG);
  const off_t unrepresentable = static_cast<off_t>(std::numeric_limits<size_t>::max()
      / sizeof(void *)) * CHUNK_SIZE;
  const off_t maximum_size = static_cast<off_t>(std::numeric_limits<blkcnt_t>::max()) * 512;
  assert(ftruncate(fd, maximum_size) == 0);
  checkZeros(fd, maximum_size - 3, 3);
  struct stat st;
  assert(fstat(fd, &st) == 0);
  assert(st.st_size == maximum_size);
  assert(st.st_blocks == std::numeric_limits<blkcnt_t>::max());
  checkWorkerStat(fd, maximum_size);
  errno = 0;
  assert(ftruncate(fd, maximum_size + 1) == -1 && errno == EFBIG);
  checkWorkerStat(fd, maximum_size);
  errno = 0;
  assert(pwrite(fd, marker, 1, unrepresentable) == -1);
  assert(errno == EFBIG);
  errno = 0;
  assert(ftruncate(fd, -1) == -1 && errno == EINVAL);
  assert(close(fd) == 0);
}

void testAllocationFailures()
{
  const int fd = openFile("/chunked/failures");
  const uint8_t marker = 83;
  int failures = allocation_failures.load();
  realloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, 0) == -1 && errno == ENOMEM);
  realloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, 0);
  calloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, 0) == -1 && errno == ENOMEM);
  calloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, 0);
  assert(ftruncate(fd, 0) == 0);
  realloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, 0) == -1 && errno == ENOMEM);
  realloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, 0);
  assert(pwrite(fd, &marker, 1, 0) == 1);
  realloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, CHUNK_SIZE * 32) == -1 && errno == ENOMEM);
  realloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, 1);
  uint8_t readback = 0;
  assert(pread(fd, &readback, 1, 0) == 1 && readback == marker);
  assert(ftruncate(fd, 0) == 0);
  std::vector<uint8_t> bytes(3 * CHUNK_SIZE, 0x5c);
  calloc_remaining.store(1);
  assert(pwrite(fd, bytes.data(), bytes.size(), 0) == CHUNK_SIZE);
  calloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, CHUNK_SIZE);
  std::vector<uint8_t> actual(CHUNK_SIZE);
  assert(pread(fd, actual.data(), actual.size(), 0) == CHUNK_SIZE);
  assert(std::equal(actual.begin(), actual.end(), bytes.begin()));
  assert(ftruncate(fd, bytes.size()) == 0);
  checkZeros(fd, CHUNK_SIZE, 2 * CHUNK_SIZE);
  assert(pwrite(fd, bytes.data(), bytes.size(), 0) == static_cast<ssize_t>(bytes.size()));
  checkWorkerStat(fd, bytes.size());
  assert(close(fd) == 0);
}

void testZeroWrites()
{
  const int fd = openFile("/chunked/zeros");
  std::vector<uint8_t> zeros(CHUNK_SIZE, 0);
  const off_t size = 256 * 1024 * 1024;
  int failures = allocation_failures.load();
  const size_t heap_size = emscripten_get_heap_size();
  calloc_remaining.store(0);
  realloc_remaining.store(0);
  for (off_t offset = 0; offset < size; offset += CHUNK_SIZE) {
    assert(pwrite(fd, zeros.data(), zeros.size(), offset) == CHUNK_SIZE);
  }
  const off_t distant = (off_t{1} << 32) + 37;
  assert(pwrite(fd, zeros.data(), zeros.size(), distant) == CHUNK_SIZE);
  assert(allocation_failures.load() == failures);
  assert(emscripten_get_heap_size() == heap_size);
  calloc_remaining.store(-1);
  realloc_remaining.store(-1);
  checkWorkerStat(fd, distant + CHUNK_SIZE);
  checkZeros(fd, 0, CHUNK_SIZE);
  checkZeros(fd, size / 2, CHUNK_SIZE);
  checkZeros(fd, size - CHUNK_SIZE, CHUNK_SIZE);
  checkZeros(fd, distant - 1, CHUNK_SIZE + 1);
  const uint8_t marker = 93;
  realloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, 0) == -1 && errno == ENOMEM);
  realloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, distant + CHUNK_SIZE);
  assert(ftruncate(fd, 0) == 0);

  std::vector<uint8_t> expected(3 * CHUNK_SIZE, 0x5c);
  assert(pwrite(fd, expected.data(), expected.size(), 0) == static_cast<ssize_t>(expected.size()));
  assert(pwrite(fd, zeros.data(), zeros.size(), CHUNK_SIZE) == CHUNK_SIZE);
  std::fill(expected.begin() + CHUNK_SIZE, expected.begin() + 2 * CHUNK_SIZE, 0);
  calloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, CHUNK_SIZE) == -1 && errno == ENOMEM);
  calloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, expected.size());
  assert(pwrite(fd, zeros.data(), 29, CHUNK_SIZE - 17) == 29);
  std::fill(expected.begin() + CHUNK_SIZE - 17, expected.begin() + CHUNK_SIZE, 0);
  assert(pwrite(fd, zeros.data(), CHUNK_SIZE - 2, 2 * CHUNK_SIZE + 1) == CHUNK_SIZE - 2);
  std::fill(expected.begin() + 2 * CHUNK_SIZE + 1, expected.end() - 1, 0);
  std::vector<uint8_t> actual(expected.size());
  assert(pread(fd, actual.data(), actual.size(), 0) == static_cast<ssize_t>(actual.size()));
  assert(actual == expected);
  assert(pwrite(fd, zeros.data(), 1, 2 * CHUNK_SIZE) == 1);
  assert(pwrite(fd, zeros.data(), 1, 3 * CHUNK_SIZE - 1) == 1);
  calloc_remaining.store(0);
  errno = 0;
  assert(pwrite(fd, &marker, 1, 2 * CHUNK_SIZE) == -1 && errno == ENOMEM);
  calloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, expected.size());
  checkZeros(fd, CHUNK_SIZE, 2 * CHUNK_SIZE);
  assert(pread(fd, actual.data(), CHUNK_SIZE, 0) == CHUNK_SIZE);
  assert(std::equal(actual.begin(), actual.begin() + CHUNK_SIZE, expected.begin()));

  assert(ftruncate(fd, 0) == 0);
  std::vector<uint8_t> mixed(3 * CHUNK_SIZE, 0);
  mixed[CHUNK_SIZE] = marker;
  calloc_remaining.store(0);
  assert(pwrite(fd, mixed.data(), mixed.size(), 0) == CHUNK_SIZE);
  calloc_remaining.store(-1);
  assert(allocation_failures.load() == ++failures);
  checkWorkerStat(fd, CHUNK_SIZE);
  checkZeros(fd, 0, CHUNK_SIZE);
  assert(pwrite(fd, mixed.data(), mixed.size(), 0) == static_cast<ssize_t>(mixed.size()));
  actual.resize(mixed.size());
  assert(pread(fd, actual.data(), actual.size(), 0) == static_cast<ssize_t>(actual.size()));
  assert(actual == mixed);
  assert(close(fd) == 0);
}

}

extern "C" void *__wrap_calloc(size_t count, size_t size)
{
  if ((count == CHUNK_SIZE && size == 1) || (count == 1 && size == CHUNK_SIZE)) {
    if (shouldFail(calloc_remaining)) {
      return nullptr;
    }
  }
  return __real_calloc(count, size);
}

extern "C" void *__wrap_realloc(void *ptr, size_t size)
{
  return shouldFail(realloc_remaining) ? nullptr : __real_realloc(ptr, size);
}

int main()
{
  static_assert(sizeof(off_t) == 8);
  static_assert(sizeof(size_t) == 4);
  backend_t backend = seekdb_create_memory_backend();
  assert(backend != nullptr);
  assert(wasmfs_create_directory("/chunked", 0777, backend) == 0);
  pthread_t worker;
  assert(pthread_create(&worker, nullptr, statWorker, nullptr) == 0);
  const int ready_fd = openFile("/chunked/ready");
  checkWorkerStat(ready_fd, 0);
  assert(close(ready_fd) == 0);
  testGrowthAndTruncate();
  testSparseAndLargeOffsets();
  testAllocationFailures();
  testZeroWrites();
  worker_request.store(-1);
  assert(pthread_join(worker, nullptr) == 0);
  std::puts("chunked memory backend: growth, sparse files, zero writes, truncate, 64-bit offsets, allocation failures and cross-thread fstat passed");
  return 0;
}
