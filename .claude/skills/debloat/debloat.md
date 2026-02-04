# debloat

Skill for analyzing and reducing the text segment size of the seekdb executable.

## When to Use

Use this skill when:
- Analyzing what contributes to text segment size
- Identifying bloated template instantiations
- Finding opportunities to reduce code size
- Evaluating compiler flags for size optimization
- Investigating symbol visibility and linkage

## Analysis Tools

### Size Analysis Commands

```bash
# Overall section sizes
size -A <executable>

# Detailed text segment breakdown
readelf -S <executable> | grep -E '\.text|\.rodata'

# Symbol sizes sorted by size (text symbols only)
nm --size-sort --print-size <executable> | grep ' [Tt] ' | tail -50

# Using bloaty for detailed analysis (if available)
bloaty -d symbols <executable> | head -100

# Find largest object files in build
find . -name '*.o' -exec size {} \; | sort -k1 -n -r | head -50

# Template instantiation analysis - find mangled template symbols
nm <executable> | c++filt | grep -E '<.*>' | cut -d' ' -f3 | sort | uniq -c | sort -rn | head -50
```

### Identifying Template Bloat

```bash
# Count instantiations per template
nm <executable> | c++filt | sed -n 's/.*\(<[^>]*>\).*/\1/p' | sort | uniq -c | sort -rn | head -30

# Find large template functions
nm --size-sort --print-size <executable> | c++filt | grep '<' | tail -30

# Analyze specific template families
nm <executable> | c++filt | grep 'std::vector' | wc -l
nm <executable> | c++filt | grep 'std::map' | wc -l
nm <executable> | c++filt | grep 'std::function' | wc -l
```

## Reduction Techniques

### 1. Compiler Flags

```cmake
# Size optimization flags
-Os                    # Optimize for size
-ffunction-sections    # Place each function in its own section
-fdata-sections        # Place each data item in its own section
-Wl,--gc-sections      # Remove unused sections at link time
-fno-exceptions        # Disable exceptions (if not used)
-fno-rtti              # Disable RTTI (if not used)
-fvisibility=hidden    # Hide symbols by default
-fvisibility-inlines-hidden  # Hide inline function symbols
```

### 2. Template Instantiation Control

**Explicit Instantiation Declaration (extern template)**
```cpp
// In header - prevent implicit instantiation
extern template class std::vector<int>;
extern template class std::map<std::string, int>;

// In one .cpp file - provide single instantiation
template class std::vector<int>;
template class std::map<std::string, int>;
```

**Template Bloat Patterns to Avoid**
- Large inline template functions
- Deep template nesting
- Unnecessary template parameters (use type erasure)
- Header-only libraries with heavy templates

### 3. Inline Function Control

```cpp
// Prevent inlining of large functions
__attribute__((noinline)) void large_function();

// Or use compiler flag for specific files
-fno-inline-functions
```

### 4. Symbol Visibility

```cpp
// Default hidden visibility
#pragma GCC visibility push(hidden)
// ... internal code ...
#pragma GCC visibility pop

// Export only public API
__attribute__((visibility("default"))) void public_api();
```

### 5. Link-Time Optimization (LTO)

```cmake
# Enable LTO for better dead code elimination
-flto
-flto=thin  # Faster compilation, similar results
```

### 6. Code Deduplication

- Use `-fmerge-all-constants`
- Enable ICF (Identical Code Folding): `-Wl,--icf=all`
- Use COMDAT folding for templates

## Investigation Workflow

1. **Baseline Measurement**
   ```bash
   size <executable>
   # Record: text, data, bss, total
   ```

2. **Identify Top Contributors**
   ```bash
   nm --size-sort --print-size <executable> | tail -100 > top_symbols.txt
   ```

3. **Categorize by Source**
   - Template instantiations
   - Inline functions
   - Static/anonymous namespace bloat
   - Third-party library code

4. **Target High-Impact Areas**
   - Focus on symbols > 10KB
   - Look for repeated patterns (same template, many types)
   - Check for debug/logging code that could be compiled out

5. **Measure After Changes**
   - Rebuild and compare section sizes
   - Verify functionality is preserved

## Project-Specific Considerations

For seekdb (OceanBase-derived codebase):
- Heavy use of macros for serialization (OB_SERIALIZE_MEMBER)
- RPC framework generates significant template code
- Observer pattern with many virtual functions
- SQL execution engine has many operator types

Focus areas:
- `src/sql/` - SQL layer templates
- `src/storage/` - Storage engine code
- `src/share/` - Shared utilities and templates
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
| Remove RTTI | Low | Low | High |
| Remove exceptions | Low | Low | High |
