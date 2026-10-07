#pragma once
#import <Foundation/Foundation.h>
#include <functional>
#include "driver.h"

/** Run an explicitly requested bounded socket workload, or verify its counter after restart. */
bool run_framework_stability(const Driver &driver, SeekdbHandle handle, SeekdbConnection connection,
                             NSMutableDictionary *report, const std::function<void()> &checkpoint);
