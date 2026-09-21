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
#include <cstdio>
#include <fcntl.h>
#include <memory>
#include <unistd.h>
#include "memory_backend.h"
#include "wasmfs.h"

extern "C" int __syscall_dup3(int oldfd, int newfd, int flags);

namespace {
struct FileCounts {
  int opens = 0;
  int closes = 0;
  int active = 0;
  int close_error = 0;
};

class CountedFile final : public wasmfs::MemoryDataFile {
public:
  explicit CountedFile(std::shared_ptr<FileCounts> counts)
      : MemoryDataFile(0600, nullptr), counts_(std::move(counts)) {}

private:
  int open(wasmfs::oflags_t) override
  {
    ++counts_->opens;
    ++counts_->active;
    return 0;
  }

  int close() override
  {
    assert(counts_->active > 0);
    --counts_->active;
    ++counts_->closes;
    return counts_->close_error;
  }

  std::shared_ptr<FileCounts> counts_;
};

std::shared_ptr<FileCounts> install(const char *name)
{
  auto counts = std::make_shared<FileCounts>();
  assert(wasmfs::wasmFS.getRootDirectory()->locked().mountChild(
      name, std::make_shared<CountedFile>(counts)));
  return counts;
}

void different_files()
{
  const auto source = install("dup-source");
  const auto target = install("dup-target");
  const int from = open("/dup-source", O_RDWR);
  const int to = open("/dup-target", O_RDWR);
  assert(from >= 0 && to >= 0);
  assert(dup2(from, to) == to);
  assert(target->closes == 1 && target->active == 0);
  assert(source->opens == 1 && source->closes == 0);
  assert(close(from) == 0);
  assert(source->active == 1 && source->closes == 0);
  assert(write(to, "a", 1) == 1);
  assert(close(to) == 0);
  assert(source->closes == 1 && source->active == 0);
}

void independent_opens_of_one_file()
{
  const auto counts = install("dup-independent");
  const int from = open("/dup-independent", O_RDWR);
  const int to = open("/dup-independent", O_RDWR);
  assert(from >= 0 && to >= 0 && counts->active == 2);
  assert(dup2(from, to) == to);
  assert(counts->opens == 2 && counts->closes == 1 && counts->active == 1);
  assert(close(from) == 0);
  assert(counts->active == 1);
  assert(close(to) == 0);
  assert(counts->closes == 2 && counts->active == 0);
}

void descriptor_aliases()
{
  const auto counts = install("dup-aliases");
  const auto replacement = install("dup-replacement");
  const int from = open("/dup-aliases", O_RDWR);
  const int to = dup(from);
  const int third = dup(to);
  const int other = open("/dup-replacement", O_RDWR);
  assert(from >= 0 && to >= 0 && third >= 0 && other >= 0);
  assert(dup2(from, to) == to);
  assert(counts->opens == 1 && counts->closes == 0 && counts->active == 1);
  assert(dup2(other, third) == third);
  assert(counts->closes == 0 && counts->active == 1);
  assert(close(from) == 0 && close(to) == 0);
  assert(counts->closes == 1 && counts->active == 0);
  assert(close(third) == 0 && replacement->active == 1);
  assert(close(other) == 0);
  assert(replacement->closes == 1 && replacement->active == 0);
}

void invalid_arguments_preserve_target()
{
  const auto counts = install("dup-invalid");
  const int fd = open("/dup-invalid", O_RDWR);
  assert(fd >= 0);
  assert(__syscall_dup3(-1, fd, 0) == -EBADF);
  assert(__syscall_dup3(WASMFS_FD_MAX, fd, 0) == -EBADF);
  assert(__syscall_dup3(fd, -1, 0) == -EBADF);
  assert(__syscall_dup3(fd, WASMFS_FD_MAX, 0) == -EBADF);
  assert(__syscall_dup3(fd, fd, 0) == -EINVAL);
  assert(__syscall_dup3(fd, fd + 1, O_APPEND) == -EINVAL);
  assert(__syscall_dup3(fd, fd + 1, O_CLOEXEC | O_APPEND) == -EINVAL);
  assert(dup2(fd, fd) == fd);
  assert(counts->opens == 1 && counts->closes == 0 && counts->active == 1);
  assert(write(fd, "b", 1) == 1);
  assert(close(fd) == 0);
  assert(counts->closes == 1 && counts->active == 0);
}

void close_error_and_cloexec()
{
  const auto source = install("dup-close-error-source");
  const auto target = install("dup-close-error-target");
  const int from = open("/dup-close-error-source", O_RDWR);
  const int to = open("/dup-close-error-target", O_RDWR);
  assert(from >= 0 && to >= 0);
  target->close_error = -EIO;
  assert(__syscall_dup3(from, to, O_CLOEXEC) == to);
  assert(target->closes == 1 && target->active == 0);
  assert(write(to, "c", 1) == 1);
  assert(close(from) == 0 && close(to) == 0);
  assert(source->closes == 1 && source->active == 0);
}
}

int main()
{
  different_files();
  independent_opens_of_one_file();
  descriptor_aliases();
  invalid_arguments_preserve_target();
  close_error_and_cloexec();
  std::puts("PASS: WasmFS dup2/dup3 closes overwritten files and preserves descriptor aliases");
}
