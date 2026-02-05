# Finding Template Instantiations

## Method: Binary Analysis

Analyze the compiled binary to find real template instantiations (more accurate than source grep).

## Output Filename Convention

| Part | Filename Format | Example |
|------|-----------------|---------|
| Part 1 | `TemplateName.txt` | `ObArray.txt`, `ObIArray.txt` |
| Part 3 | `Template<Type>.txt` | `ObIArray<oceanbase::sql::ObRawExpr_ptr>.txt` |

Note: Use `_ptr` instead of `*` in filenames to avoid shell issues.

## Part 1: Calculate Size Per Template Instantiation

```bash
nm --size-sort --print-size <exe> 2>/dev/null | c++filt | grep 'TemplateName<' | \
    python3 .claude/skills/debloat/template_size_analysis.py TemplateName
```

Example for ObArray:
```bash
nm --size-sort --print-size build_debug/src/observer/observer 2>/dev/null | c++filt | grep 'ObArray<' | \
    python3 .claude/skills/debloat/template_size_analysis.py ObArray > ObArray.txt
```

Sample output:
```
  SIZE(KB) COUNT  TEMPLATE INSTANTIATION
  -------- -----  --------------------------------------------------
    210.93   341  ObArray<unsigned long, oceanbase::common::ModulePageAllocator, false>
    154.29   248  ObArray<long, oceanbase::common::ModulePageAllocator, false>
    148.56   295  ObArray<oceanbase::common::ObTabletID, oceanbase::common::ModulePageAllocator, false>
    ...

  -------- -----
   3495.36 14103  TOTAL (998 unique)
```

## Part 2: Size and Count for All Template Classes

```bash
nm --size-sort --print-size <exe> 2>/dev/null | c++filt | \
    python3 .claude/skills/debloat/template_class_size.py > output3.txt
```

Sample output:
```
  SIZE(KB)  COUNT  TEMPLATE
  --------  -----  --------------------
  28713.23    560  EvalVectorCmp
  28533.01   1315  BatchAggregateWrapper
  28431.55    119  VectorOpUtil
  24523.58   1852  ObIArray
   2709.03   1558  ObSEArray
   2471.74   1015  ObArray
   ...
```

- `SIZE(KB)`: Total size in text segment
- `COUNT`: Number of unique template instantiations

## Part 3: Direct Methods of a Specific Template Instantiation

Show the actual methods generated for a specific template instantiation (e.g., `ObIArray<oceanbase::sql::ObRawExpr*>`).

```bash
nm --print-size <exe> 2>/dev/null | c++filt | \
    python3 .claude/skills/debloat/template_size_analysis.py --methods "Template<Type>"
```

Example:
```bash
nm --print-size build_debug/src/observer/observer 2>/dev/null | c++filt | \
    python3 .claude/skills/debloat/template_size_analysis.py --methods "ObIArray<oceanbase::sql::ObRawExpr*>" \
    > "ObIArray<oceanbase::sql::ObRawExpr_ptr>.txt"
```

Sample output:
```
Direct methods of: ObIArray<oceanbase::sql::ObRawExpr*>
================================================================================

Raw symbols:
--------------------------------------------------------------------------------
00000000084ee430 0000000000000080 W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::alloc_place_holder()  [128 bytes]
00000000084ee2e0 0000000000000041 W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::ObIArray(oceanbase::sql::ObRawExpr**, long)  [65 bytes]
00000000090c6340 0000000000000031 W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::ObIArray()  [49 bytes]
00000000084ee420 000000000000000a W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::~ObIArray()  [10 bytes]
00000000084ee400 000000000000001b W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::~ObIArray()  [27 bytes]
00000000084ee4b0 000000000000015f W oceanbase::common::ObIArray<oceanbase::sql::ObRawExpr*>::to_string(char*, long) const  [351 bytes]

   SIZE(B) COUNT  METHOD
  -------- -----  --------------------------------------------------
       351     1  to_string
       128     1  alloc_place_holder
       114     2  ObIArray
        37     2  ~ObIArray

  -------- -----
       630     6  TOTAL (4 unique methods)
      0.62 KB
```

Note: Multiple destructor/constructor entries are due to Itanium C++ ABI variants (D0/D1/D2 for destructors, C1/C2/C3 for constructors).
