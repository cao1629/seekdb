// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Engine states for the experimental, single-run-per-process iOS host. */
enum seekdb_ios_state {
  SEEKDB_IOS_IDLE = 0,
  SEEKDB_IOS_STARTING = 1,
  SEEKDB_IOS_RUNNING = 2,
  SEEKDB_IOS_STOPPING = 3,
  SEEKDB_IOS_STOPPED = 4,
  SEEKDB_IOS_FAILED = 5
};

/** Completed cleanup actions for the current process run. */
enum seekdb_ios_cleanup_status {
  SEEKDB_IOS_CLEANUP_NONE = 0,
  SEEKDB_IOS_CLEANUP_SERVER = 1 << 0,
  SEEKDB_IOS_CLEANUP_CURL = 1 << 1,
  SEEKDB_IOS_CLEANUP_WORKING_DIRECTORY = 1 << 2
};

/**
 * Run the engine synchronously on a dedicated background thread until stopped.
 * The absolute directory must be inside the app's writable sandbox. This changes
 * the process working directory for the engine's lifetime and restores it
 * before returning, including after startup failure. A failed run cannot be
 * retried in the same process.
 * Uses a 1 GiB logical memory budget, a 128 MiB vector allocation limit, and
 * 2 GiB redo space, with TCP disabled; clients use
 * the engine's Unix socket. Returns an engine error code, or zero on clean stop.
 * Only one invocation is supported per app process. Never call on the UI thread.
 */
int seekdb_ios_run(const char *absolute_directory);

/** Request shutdown; safe from another thread, including during startup. */
void seekdb_ios_request_stop(void);

/** Return the current lifecycle state without blocking. */
enum seekdb_ios_state seekdb_ios_get_state(void);

/** Return completed cleanup actions for the current process run. */
unsigned int seekdb_ios_get_cleanup_status(void);

/** Return the first cleanup error without replacing the primary runtime error. */
int seekdb_ios_get_cleanup_error(void);

/** Return the source revision compiled into the linked runtime archive. */
const char *seekdb_ios_get_build_id(void);

/** Return whether deterministic test hooks were compiled into the runtime. */
const char *seekdb_ios_get_hook_mode(void);

#ifdef __cplusplus
}
#endif
