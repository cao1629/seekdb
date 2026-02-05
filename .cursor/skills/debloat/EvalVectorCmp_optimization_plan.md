# EvalVectorCmp Template Optimization Plan

## Goal
Reduce template instantiations from ~560 to ~80 (7x reduction) by making `cmp_op` a runtime parameter instead of a compile-time template parameter.

## Current State
- `EvalVectorCmp<l_tc, r_tc, cmp_op>` has 3 template parameters
- `EVAL_VECTOR_EXPR_CMP_FUNCS[MAX_VEC_TC][MAX_VEC_TC][CO_MAX]` is a 3D array
- ~80 valid type pairs x 7 operators = 560 instantiations (~28.7 MB)

## Approach: Store cmp_op in ObExpr::extra_

The `ObExpr::extra_` union has 46 reserved bits. Use 3 bits (for 7 values) to store `cmp_op` at code generation time, then read it at runtime.

---

## Implementation Steps

### Step 1: Add runtime get_cmp_ret function
**File:** `src/share/vector/expr_cmp_func.ipp` (after line 248)

```cpp
OB_INLINE int get_cmp_ret_runtime(const ObCmpOp cmp_op, const int cmp_ret)
{
  switch (cmp_op) {
    case CO_EQ:  return cmp_ret == 0;
    case CO_LE:  return cmp_ret <= 0;
    case CO_LT:  return cmp_ret < 0;
    case CO_GE:  return cmp_ret >= 0;
    case CO_GT:  return cmp_ret > 0;
    case CO_NE:  return cmp_ret != 0;
    case CO_CMP: return cmp_ret;
    default:     return 0;
  }
}
```

### Step 2: Modify EvalVectorCmp template
**File:** `src/share/vector/expr_cmp_func.ipp` (line 326)

Change from:
```cpp
template <VecValueTypeClass l_tc, VecValueTypeClass r_tc, ObCmpOp cmp_op>
struct EvalVectorCmp
```

To:
```cpp
template <VecValueTypeClass l_tc, VecValueTypeClass r_tc>
struct EvalVectorCmp
```

Add at start of `eval_vector`:
```cpp
const ObCmpOp cmp_op = static_cast<ObCmpOp>(expr.extra_ & 0x7);
```

### Step 3: Update DO_VECTOR_CMP macro
**File:** `src/share/vector/expr_cmp_func.ipp` (lines 275, 293, 316)

Replace all:
```cpp
res_vec->set_int(i, get_cmp_ret<cmp_op>(cmp_ret));
```

With:
```cpp
res_vec->set_int(i, get_cmp_ret_runtime(cmp_op, cmp_ret));
```

### Step 4: Update NULL specializations
**File:** `src/share/vector/expr_cmp_func.ipp` (lines 433-440)

Change from:
```cpp
template<VecValueTypeClass l_tc, ObCmpOp cmp_op>
struct EvalVectorCmp<l_tc, VEC_TC_NULL, cmp_op>: public EvalVectorCmpWithNull {};
```

To:
```cpp
template<VecValueTypeClass l_tc>
struct EvalVectorCmp<l_tc, VEC_TC_NULL>: public EvalVectorCmpWithNull {};
```

(Similar for other NULL specializations)

### Step 5: Change array from 3D to 2D
**File:** `src/share/vector/expr_cmp_func.cpp` (line 32)

Change:
```cpp
sql::ObExpr::EvalVectorFunc EVAL_VECTOR_EXPR_CMP_FUNCS[MAX_VEC_TC][MAX_VEC_TC][CO_MAX];
```

To:
```cpp
sql::ObExpr::EvalVectorFunc EVAL_VECTOR_EXPR_CMP_FUNCS[MAX_VEC_TC][MAX_VEC_TC];
```

**File:** `src/share/vector/expr_cmp_func.ipp` (line 43) - update extern declaration

### Step 6: Update VectorExprCmpFuncIniter
**File:** `src/share/vector/expr_cmp_func.ipp` (lines 450-467)

Change to register single function per type pair:
```cpp
template<int X, int Y>
struct VectorExprCmpFuncIniter<X, Y, true>
{
  using EvalFunc = EvalVectorCmp<static_cast<VecValueTypeClass>(X),
                                 static_cast<VecValueTypeClass>(Y)>;
  static void init_array()
  {
    EVAL_VECTOR_EXPR_CMP_FUNCS[X][Y] = &EvalFunc::eval_vector;
  }
};
```

### Step 7: Update lookup function
**File:** `src/share/vector/expr_cmp_func.cpp` (lines 85-92)

Change to return from 2D array (cmp_op no longer used for lookup):
```cpp
return EVAL_VECTOR_EXPR_CMP_FUNCS[l_tc][r_tc];
```

### Step 8: Store cmp_op in extra_
**File:** `src/sql/engine/expr/ob_expr_operator.cpp` (after line 6553)

Add:
```cpp
// Store cmp_op in extra_ for runtime access (uses 3 bits of reserved_)
rt_expr.extra_ = (rt_expr.extra_ & ~0x7ULL) | static_cast<uint64_t>(cmp_op);
```

### Step 9: Update serialization
**File:** `src/share/vector/expr_cmp_func.cpp` (lines 98-110)

Update array size and initialization for 2D array.

### Step 10: Update SIMD version
**File:** `src/share/vector/expr_cmp_func_simd.ipp`

Apply similar changes to `FixedVectorCmp` and `FixedExprCmpFuncIniter`.

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/share/vector/expr_cmp_func.ipp` | Template changes, macro update, get_cmp_ret_runtime |
| `src/share/vector/expr_cmp_func_simd.ipp` | FixedVectorCmp template changes |
| `src/share/vector/expr_cmp_func.cpp` | Array 3D→2D, serialization, lookup function |
| `src/sql/engine/expr/ob_expr_operator.cpp` | Store cmp_op in extra_ |

---

## Verification

1. **Build test:**
   ```bash
   cd build_debug && make -j observer
   ```

2. **Check text segment reduction:**
   ```bash
   size build_debug/src/observer/observer
   # Compare text segment with baseline (666 MB)
   ```

3. **Run template analysis:**
   ```bash
   nm --size-sort --print-size build_debug/src/observer/observer | c++filt | grep 'EvalVectorCmp<' | python3 .claude/skills/debloat/template_size_analysis.py EvalVectorCmp
   # Should show ~80 instantiations instead of 560
   ```

4. **Functional test:**
   Run comparison operator tests to verify correctness.

---

## Expected Results

- Template instantiations: 560 → 80 (7x reduction)
- Text segment reduction: ~24 MB (from EvalVectorCmp alone)
- Runtime overhead: Negligible (well-predicted switch)

---

## Baseline (2026-02-02)

```
Text segment: 698,407,324 bytes (666.14 MB)
EvalVectorCmp: 28,713 KB (560 instantiations)
```
