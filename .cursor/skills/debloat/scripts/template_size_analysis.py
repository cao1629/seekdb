#!/usr/bin/env python3
"""
Aggregate template instantiation sizes from nm output.

Usage:
    Part 1 - Aggregate sizes:
    nm --size-sort --print-size <exe> | c++filt | grep 'ObVector<' | python3 template_size_analysis.py [template_name]

    Part 3 - List direct template methods:
    nm --print-size <exe> | c++filt | python3 template_size_analysis.py --methods "ObIArray<oceanbase::sql::ObRawExpr*>"

Example:
    nm --size-sort --print-size build_debug/src/observer/observer | c++filt | grep 'ObVector<' | python3 template_size_analysis.py ObVector
    nm --print-size build_debug/src/observer/observer | c++filt | python3 template_size_analysis.py --methods "ObIArray<oceanbase::sql::ObRawExpr*>"
"""

import sys
import re
from collections import defaultdict


def extract_template(symbol, template_name, include_namespace=True):
    """Extract template instantiation with balanced brackets.

    Handles arbitrary nesting depth like:
    BatchAggregateWrapper<DistinctWrapper<SumOpNSize<int, bool>>>

    If include_namespace=True, includes the namespace prefix like:
    oceanbase::common::EvalVectorCmp<...>
    """
    # Find template_name<
    start_pattern = template_name + '<'
    start_idx = symbol.find(start_pattern)
    if start_idx == -1:
        return None

    # Start after template_name, at the '<'
    bracket_start = start_idx + len(template_name)
    depth = 0
    i = bracket_start

    while i < len(symbol):
        if symbol[i] == '<':
            depth += 1
        elif symbol[i] == '>':
            depth -= 1
            if depth == 0:
                # Found matching closing bracket
                template_end = i + 1

                if include_namespace:
                    # Walk backwards from start_idx to find namespace prefix
                    ns_start = start_idx
                    while ns_start > 0 and (symbol[ns_start-1].isalnum() or symbol[ns_start-1] in '_:'):
                        ns_start -= 1
                    return symbol[ns_start:template_end]
                else:
                    return symbol[start_idx:template_end]
        i += 1

    return None  # Unbalanced brackets


def aggregate_sizes(lines, template_pattern="ObVector"):
    """Aggregate sizes by template instantiation from nm output."""
    template_sizes = defaultdict(lambda: {"size": 0, "count": 0})

    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            try:
                size = int(parts[1], 16)
                symbol = " ".join(parts[3:])
                tmpl = extract_template(symbol, template_pattern)
                if tmpl:
                    template_sizes[tmpl]["size"] += size
                    template_sizes[tmpl]["count"] += 1
            except (ValueError, IndexError):
                continue

    return template_sizes


def print_results(template_sizes):
    """Print aggregated results sorted by size."""
    sorted_templates = sorted(
        template_sizes.items(),
        key=lambda x: x[1]["size"],
        reverse=True
    )

    print(f"{'SIZE(KB)':>10} {'METHOD COUNT':>12}  TEMPLATE INSTANTIATION")
    print(f"{'-'*8:>10} {'-'*12:>12}  {'-'*50}")

    total_size = 0
    total_count = 0

    for tmpl, data in sorted_templates:
        size_kb = data["size"] / 1024
        count = data["count"]
        total_size += data["size"]
        total_count += count
        print(f"{size_kb:>10.2f} {count:>12}  {tmpl}")

    print(f"\n{'-'*8:>10} {'-'*12:>12}")
    print(f"{total_size/1024:>10.2f} {total_count:>12}  TOTAL ({len(sorted_templates)} unique)")


def extract_methods(lines, template_inst):
    """Extract direct methods of a specific template instantiation.

    E.g., for "ObIArray<oceanbase::sql::ObRawExpr*>", find symbols like:
    oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::method()
    """
    methods = []
    raw_lines = []
    # Escape special regex chars but keep structure
    escaped = re.escape(template_inst)
    # Pattern: namespace::Template<T>::method
    pattern = rf'(\S*::{escaped}::(\S+))'

    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            try:
                size = int(parts[1], 16)
                symbol = " ".join(parts[3:])
                match = re.search(pattern, symbol)
                if match:
                    method_name = match.group(2)
                    # Extract just method name without params for grouping
                    method_base = method_name.split('(')[0]
                    methods.append({
                        "size": size,
                        "method": method_base,
                        "full_symbol": symbol
                    })
                    raw_lines.append(line)
            except (ValueError, IndexError):
                continue

    return methods, raw_lines


def print_methods(template_inst, methods, raw_lines):
    """Print direct template methods sorted by size."""
    print(f"Direct methods of: {template_inst}")
    print(f"{'='*80}")

    # Print raw symbol lines with size in bytes
    total_size = 0
    for line in raw_lines:
        parts = line.split()
        if len(parts) >= 2:
            try:
                size_bytes = int(parts[1], 16)
                total_size += size_bytes
                print(f"{line.rstrip()}  [{size_bytes} bytes]")
            except ValueError:
                print(line.rstrip())
        else:
            print(line.rstrip())

    print(f"\n{'='*80}")
    print(f"TOTAL: {len(raw_lines)} symbols, {total_size} bytes ({total_size/1024:.2f} KB)")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--methods":
        # Part 3: List direct template methods
        template_inst = sys.argv[2]
        lines = sys.stdin.readlines()
        methods, raw_lines = extract_methods(lines, template_inst)
        print_methods(template_inst, methods, raw_lines)
    else:
        # Part 1: Aggregate sizes from nm output
        template = sys.argv[1] if len(sys.argv) > 1 else "ObVector"
        lines = sys.stdin.readlines()
        template_sizes = aggregate_sizes(lines, template)
        print_results(template_sizes)
