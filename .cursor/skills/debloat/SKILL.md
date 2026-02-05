---
name: debloat
description: Analyzes and reduces text segment size of the seekdb executable. Use when investigating binary size, identifying template bloat, finding large symbols, optimizing code size, or when the user mentions binary size, text segment, template instantiations, or symbol visibility.
---

# Binary Size Debloat

Skill for analyzing and reducing the text segment size of the seekdb executable.

## Quick Start

### Baseline Measurement

```bash
size build_debug/src/observer/observer
# Record: text, data, bss, total
```

### Find Largest Symbols

```bash
nm --size-sort --print-size build_debug/src/observer/observer | grep ' [Tt] ' | tail -50
```

### Identify Template Bloat

```bash
nm build_debug/src/observer/observer | c++filt | grep -E '<.*>' | cut -d' ' -f3 | sort | uniq -c | sort -rn | head -50
```

## Analysis Commands

| Command | Purpose |
|---------|---------|
| `size -A <exe>` | Overall section sizes |
| `nm --size-sort --print-size <exe>` | Symbols sorted by size |
| `nm <exe> \| c++filt \| grep '<'` | Template instantiations |
| `readelf -S <exe>` | Section breakdown |

## Key Reduction Techniques

### 1. Compiler Flags (Low effort, Medium impact)

```cmake
-Os                         # Optimize for size
-ffunction-sections         # Separate sections per function
-fdata-sections             # Separate sections per data
-Wl,--gc-sections           # Remove unused sections
-fvisibility=hidden         # Hide symbols by default
```

### 2. Extern Template (Medium effort, High impact)

```cpp
// In header - prevent implicit instantiation
extern template class std::vector<int>;

// In one .cpp file - provide single instantiation
template class std::vector<int>;
```

### 3. Link-Time Optimization (Low effort, High impact)

```cmake
-flto        # Full LTO
-flto=thin   # Faster compilation
```

### 4. Code Deduplication (Low effort, Medium impact)

```cmake
-fmerge-all-constants
-Wl,--icf=all   # Identical Code Folding
```

## Investigation Workflow

1. **Measure baseline**: `size <executable>`
2. **Find top contributors**: `nm --size-sort --print-size <exe> | tail -100`
3. **Categorize**: Templates? Inline functions? Third-party code?
4. **Target high-impact**: Focus on symbols > 10KB
5. **Measure after changes**: Rebuild and compare

## Utility Scripts

```bash
# Analyze template class sizes
python scripts/template_class_size.py

# Detailed template size analysis
python scripts/template_size_analysis.py
```

## Project Focus Areas

For seekdb (OceanBase-derived):
- `src/sql/` - SQL layer templates
- `src/storage/` - Storage engine code
- `src/share/` - Shared utilities
- `deps/` - Third-party dependencies

## Quick Reference

| Technique | Effort | Impact | Risk |
|-----------|--------|--------|------|
| -Os flag | Low | Medium | Low |
| --gc-sections | Low | Medium | Low |
| extern template | Medium | High | Low |
| -fvisibility=hidden | Medium | Medium | Medium |
| LTO | Low | High | Medium |
| ICF | Low | Medium | Low |

## Additional Resources

- [EvalVectorCmp optimization plan](EvalVectorCmp_optimization_plan.md)
- [Template instantiation guide](find-template-instantiations.md)
- [Template size analysis](template-size-analysis.md)
- Analysis outputs in [analysis/](analysis/)
