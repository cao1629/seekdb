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

/**
 * Run the engine synchronously on a dedicated background thread until stopped.
 * The absolute directory must be inside the app's writable sandbox. This changes
 * the process working directory for the engine's lifetime and restores it on
 * clean return. A failed startup may retain global services and the working
 * directory until app exit; restarting the engine in that process is unsupported.
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

#ifdef __cplusplus
}
#endif
