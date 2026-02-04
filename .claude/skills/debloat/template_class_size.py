#!/usr/bin/env python3
"""
Aggregate template sizes by template name (classes and functions).

Usage:
    nm --size-sort --print-size <exe> | c++filt | python3 template_class_size.py
"""

import sys
import re
from collections import defaultdict

sizes = defaultdict(int)
instantiations = defaultdict(set)

for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 4:
        try:
            size = int(parts[1], 16)
            symbol = " ".join(parts[3:])
            # Match template name (classes and functions) and full instantiation
            match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)<[^>]+>', symbol)
            if match:
                tmpl_name = match.group(1)
                tmpl_full = match.group(0)
                sizes[tmpl_name] += size
                instantiations[tmpl_name].add(tmpl_full)
        except (ValueError, IndexError):
            continue

# Sort by size descending
sorted_sizes = sorted(sizes.items(), key=lambda x: x[1], reverse=True)

print(f"{'SIZE(KB)':>10} {'TYPE COUNT':>10}  TEMPLATE")
print(f"{'-'*8:>10} {'-'*10:>10}  {'-'*20}")

total = 0
for name, size in sorted_sizes:
    size_kb = size / 1024
    count = len(instantiations[name])
    total += size
    print(f"{size_kb:>10.2f} {count:>10}  {name}")

print(f"\n{'-'*8:>10} {'-'*10:>10}")
print(f"{total/1024:>10.2f} {'':>10}  TOTAL ({len(sorted_sizes)} templates)")
