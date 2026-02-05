# Template Size Analysis in Text Segment

## Quick Usage

```bash
nm --size-sort --print-size <exe> | c++filt | grep '<template><' | \
    python3 .claude/skills/debloat/template_size_analysis.py [template_name]
```

## Examples

```bash
# Analyze ObVector in debug build
nm --size-sort --print-size build_debug/src/observer/observer | c++filt | grep 'ObVector<' | \
    python3 .claude/skills/debloat/template_size_analysis.py ObVector

# Analyze ObHashMap
nm --size-sort --print-size build_debug/src/observer/observer | c++filt | grep 'ObHashMap<' | \
    python3 .claude/skills/debloat/template_size_analysis.py ObHashMap

# Save results
nm --size-sort --print-size build_debug/src/observer/observer | c++filt | grep 'ObVector<' | \
    python3 .claude/skills/debloat/template_size_analysis.py ObVector \
    > .claude/skills/debloat/obvector_size_analysis.txt
```

## Output Format

```
  SIZE(KB) COUNT  TEMPLATE INSTANTIATION
  -------- -----  --------------------------------------------------
     13.39    34  ObVector<unsigned long, PageArena<...>>
     12.76    24  ObVector<ObJsonPathFilterNode*, PageArena<...>>
      9.68    27  ObVector<ObNsPair*, PageArena<...>>

  -------- -----
    209.78  1495  TOTAL (72 unique)
```

## Notes

- `nm --size-sort --print-size`: outputs `address size type symbol`
- Size is in hex (field 2), convert with `strtonum("0x"$2)` or `int(x, 16)`
- `c++filt`: demangles C++ symbols
- Nested templates may truncate at first `>` - regex handles one level of nesting
