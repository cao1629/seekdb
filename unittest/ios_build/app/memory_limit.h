// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#import <Foundation/Foundation.h>

/** Touch increasing resident allocations and durably record progress until exhaustion.
 * Runs only in an explicitly selected hook-enabled test. Returns after releasing
 * mappings on allocation failure, cancellation, or the 8 GiB safety ceiling;
 * an OS termination intentionally prevents a normal return.
 */
void run_memory_limit_probe(NSString *documents, NSString *runID, NSString *dataName);
