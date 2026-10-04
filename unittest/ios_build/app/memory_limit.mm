// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "memory_limit.h"
#include "seekdb_ios.h"
#include <mach/mach.h>
#include <os/proc.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {
constexpr size_t CHUNK = 64UL * 1024 * 1024;
constexpr size_t CEILING = 8UL * 1024 * 1024 * 1024;

/** Persist one complete checkpoint atomically and sync it before touching more pages. */
bool checkpoint(NSString *path, NSMutableDictionary *value, NSString *state,
                size_t allocated, size_t attempted, int allocationError = 0)
{
  task_vm_info_data_t info{};
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &count)
      != KERN_SUCCESS) return false;
  value[@"state"] = state;
  value[@"allocated_bytes"] = @(allocated);
  value[@"attempted_bytes"] = @(attempted);
  value[@"footprint_bytes"] = @(info.phys_footprint);
  value[@"available_bytes"] = @(os_proc_available_memory());
  value[@"allocation_errno"] = @(allocationError);
  value[@"timestamp"] = @([[NSDate date] timeIntervalSince1970]);
  NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
  if (data == nil || ![data writeToFile:path options:NSDataWritingAtomic error:nil]) return false;
  const int file = open(path.fileSystemRepresentation, O_RDONLY);
  if (file < 0) return false;
  const bool synced = fsync(file) == 0;
  close(file);
  const int directory = open(path.stringByDeletingLastPathComponent.fileSystemRepresentation, O_RDONLY);
  if (directory < 0) return false;
  const bool directorySynced = fsync(directory) == 0;
  close(directory);
  return synced && directorySynced;
}

/** Fill every byte with varying data so zero-page sharing and compression cannot fake pressure. */
void fill(void *mapping, uint64_t &random)
{
  auto *words = static_cast<volatile uint64_t *>(mapping);
  for (size_t index = 0; index < CHUNK / sizeof(uint64_t); ++index) {
    random ^= random << 13;
    random ^= random >> 7;
    random ^= random << 17;
    words[index] = random;
  }
}
} // namespace

void run_memory_limit_probe(NSString *documents, NSString *runID, NSString *dataName)
{
  if (strcmp(seekdb_ios_get_hook_mode(), "enabled") != 0) return;
  NSString *path = [documents stringByAppendingPathComponent:@"memory-limit-progress.json"];
  NSMutableDictionary *value = [@{@"schema_version": @1, @"run_id": runID,
      @"build_id": [NSString stringWithUTF8String:seekdb_ios_get_build_id()],
      @"data_name": dataName, @"chunk_bytes": @(CHUNK), @"ceiling_bytes": @(CEILING),
      @"page_size": @(getpagesize()), @"outcome": @"pending"} mutableCopy];
  std::vector<void *> mappings;
  mappings.reserve(CEILING / CHUNK);
  size_t allocated = 0;
  uint64_t random = 0x9e3779b97f4a7c15ULL;
  if (!checkpoint(path, value, @"ready", 0, 0)) return;
  while (allocated < CEILING && seekdb_ios_get_state() == SEEKDB_IOS_RUNNING) {
    @autoreleasepool {
      if (!checkpoint(path, value, @"allocating", allocated, allocated + CHUNK)) break;
      void *mapping = mmap(nullptr, CHUNK, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
      if (mapping == MAP_FAILED) {
        const int allocationError = errno;
        value[@"outcome"] = @"allocation-failed";
        checkpoint(path, value, @"allocation-failed", allocated, allocated + CHUNK, allocationError);
        break;
      }
      mappings.push_back(mapping);
      fill(mapping, random);
      allocated += CHUNK;
      if (!checkpoint(path, value, @"resident", allocated, allocated)) break;
      usleep(500000);
    }
  }
  if ([value[@"outcome"] isEqual:@"pending"]) {
    value[@"outcome"] = allocated == CEILING ? @"ceiling-reached" : @"cancelled";
  }
  for (void *mapping : mappings) munmap(mapping, CHUNK);
  checkpoint(path, value, @"released", allocated, allocated, [value[@"allocation_errno"] intValue]);
}
