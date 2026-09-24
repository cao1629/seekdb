import collections
import os
import re
import subprocess
import sys

ROOT = "/Users/colin/seekdb-dev/migrate-to-rust"
EXTS = (".h", ".hpp", ".cpp", ".cc", ".c", ".ipp", ".def")
SKIP = ("src/oblib/lib/compress/zstd_1_3_8/zstd_src/",)

files = subprocess.run(["git", "-C", ROOT, "ls-files", "src"], capture_output=True, text=True).stdout.split()
files = [f for f in files if f.endswith(EXTS) and not f.startswith(SKIP)]


def strip_comments_and_strings(text):
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j < 0:
                j = n
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                j = n
            else:
                j += 2
            out.append("\n" * text.count("\n", i, j))
            i = j
            continue
        if c == '"' or c == "'":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "\n":
                    break
                j += 1
            out.append(q + q)
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


PATTERNS = {
    "class_or_struct_with_base": r"^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+(?:\w+\s+)?\w+(?:\s+final)?\s*:\s*(?:public|protected|private|virtual)?\s*[\w:<>]",
    "virtual_kw": r"\bvirtual\b",
    "pure_virtual": r"\)\s*(?:const\s*)?(?:override\s*)?=\s*0\s*;",
    "override_kw": r"\boverride\b",
    "final_kw": r"\bfinal\b",
    "dynamic_cast": r"\bdynamic_cast\s*<",
    "static_cast": r"\bstatic_cast\s*<",
    "reinterpret_cast": r"\breinterpret_cast\s*<",
    "const_cast": r"\bconst_cast\s*<",
    "template_decl": r"\btemplate\s*<",
    "template_full_spec": r"\btemplate\s*<\s*>",
    "variadic_template": r"\btypename\s*\.\.\.|\bclass\s*\.\.\.",
    "enable_if": r"\benable_if(?:_t)?\b",
    "if_constexpr": r"\bif\s+constexpr\b",
    "define": r"^\s*#\s*define\b",
    "define_function_like": r"^\s*#\s*define\s+\w+\(",
    "OB_SUCC": r"\bOB_SUCC\s*\(",
    "OB_FAIL": r"\bOB_FAIL\s*\(",
    "OB_ISNULL": r"\bOB_ISNULL\s*\(",
    "OB_NOT_NULL": r"\bOB_NOT_NULL\s*\(",
    "OB_UNLIKELY": r"\bOB_UNLIKELY\s*\(",
    "OB_LIKELY": r"\bOB_LIKELY\s*\(",
    "OB_TMP_FAIL": r"\bOB_TMP_FAIL\s*\(",
    "SMART_CALL": r"\bSMART_CALL\s*\(",
    "LOG_macros": r"\b(?:LOG_WARN|LOG_INFO|LOG_TRACE|LOG_DEBUG|LOG_ERROR|LOG_EDIAG|LOG_WDIAG|LOG_USER_ERROR|LOG_USER_WARN|LOG_DBA_WARN|LOG_DBA_ERROR|LOG_DBA_INFO|\w+_LOG)\s*\(",
    "TO_STRING_KV": r"\bTO_STRING_KV\s*\(",
    "OB_UNIS": r"\bOB_UNIS_\w+\s*\(|\bOB_SERIALIZE_MEMBER\w*\s*\(|\bOB_DEF_SERIALIZE\w*\b|\bOB_DECLARE_SERIALIZE\w*\b|\bOB_UNIS_VERSION\w*\s*\(",
    "DISALLOW_COPY_AND_ASSIGN": r"\bDISALLOW_COPY_AND_ASSIGN\s*\(",
    "placement_new": r"\bnew\s*\(\s*[\w&\->\.\[\]\+\*\s]+\)\s*[\w:]",
    "OB_NEW_family": r"\bOB_NEW\w*\s*\(|\bOB_DELETE\w*\s*\(|\bOB_NEWx\s*\(",
    "explicit_destructor_call": r"(?:->|\.)\s*~\w+\s*\(",
    "union_decl": r"\bunion\b\s*(?:\w+\s*)?\{",
    "union_kw": r"\bunion\b",
    "goto": r"\bgoto\s+\w+\s*;",
    "operator_overload": r"\boperator\s*(?:==|!=|<=|>=|<<=|>>=|<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\+\+|--|->\*?|&&|\|\||\(\s*\)|\[\s*\]|[<>+\-*/%&|^!~=,])\s*\(",
    "conversion_operator": r"\boperator\s+(?:bool|int|int64_t|uint64_t|const\s+\w+|\w+\s*\*|[A-Za-z_]\w*)\s*\(\s*\)",
    "operator_new_delete": r"\boperator\s+(?:new|delete)(?:\s*\[\s*\])?\s*\(",
    "friend": r"\bfriend\b",
    "friend_class": r"\bfriend\s+(?:class|struct)\b",
    "try": r"\btry\s*\{",
    "catch": r"\bcatch\s*\(",
    "throw": r"\bthrow\b",
    "thread_local": r"\bthread_local\b|\b__thread\b|\bRLOCAL\w*\s*\(|\b_RLOCAL\b",
    "static_local_or_member": r"^\s+static\s+(?!inline|constexpr|const\s+char\s*\*\s*\w+\s*\()(?:[\w:<>,\s\*&]+)\s+\w+\s*(?:=|;|\()",
    "volatile": r"\bvolatile\b",
    "bitfield": r"^\s*(?:u?int\d+_t|unsigned|int|uint\w*|bool|char|short|long|int64_t|uint64_t)\s+\w+\s*:\s*\d+\s*[;,]",
    "setjmp_longjmp": r"\b(?:setjmp|longjmp|sigsetjmp|siglongjmp)\s*\(",
    "alloca": r"\balloca\s*\(",
    "va_list": r"\bva_list\b",
    "lambda": r"\[\s*[=&]?\s*(?:[\w&=,\s\*this]*)\]\s*\([^)]*\)\s*(?:mutable\s*)?(?:->\s*[\w:<>]+\s*)?\{",
    "enum_class": r"\benum\s+class\b",
    "enum_plain": r"\benum\s+(?!class\b)\w+\s*(?::\s*\w+\s*)?\{|\benum\s*\{",
    "pragma_pack": r"#\s*pragma\s+pack",
    "attribute_packed": r"__attribute__\s*\(\s*\(\s*packed",
    "offsetof": r"\boffsetof\s*\(",
    "CONTAINER_OF": r"\bCONTAINER_OF\s*\(",
    "zero_length_array": r"\w+\s*\[\s*0\s*\]\s*;",
    "int128": r"\b__int128\b|\bint128_t\b|\buint128_t\b",
    "cas128": r"__sync_\w+_16\b|\bCAS128\b|\bcas128\b|__atomic_\w+_16\b|\bATOMIC_\w*128\b|\bdcas\b|\bDCAS\b",
    "long_type": r"\b(?:unsigned\s+)?long\b(?!\s+long)(?!\s+double)",
    "size_t": r"\bsize_t\b",
    "simd_intrinsics_include": r"#\s*include\s*<(?:immintrin|emmintrin|smmintrin|nmmintrin|xmmintrin|tmmintrin|avxintrin|x86intrin|arm_neon|wasm_simd128)\.h>",
    "std_sort": r"\bstd::sort\s*\(|\bstd::stable_sort\s*\(",
    "lib_ob_sort": r"\bob_sort\s*\(",
    "std_function": r"\bstd::function\s*<",
    "std_containers": r"\bstd::(?:vector|map|unordered_map|set|unordered_set|string|list|deque|pair|tuple|shared_ptr|unique_ptr)\b",
    "std_any": r"\bstd::\w+",
    "default_arg_in_decl": r"\(\s*[^()]*\b\w+\s+[&\*]?\s*\w+\s*=\s*[^,()=][^,()]*[,)]",
    "ATOMIC_macros": r"\bATOMIC_\w+\s*\(",
    "fnv_hash": r"\bfnv_hash\w*\s*\(",
    "murmurhash": r"\bmurmurhash\w*\s*\(|\bhash_murmur\s*\(|\bmurmur_hash\w*\b",
    "xxhash": r"\bXXH\w+\s*\(|\bxxhash\w*\s*\(",
    "crc64": r"\bob_crc64\w*\s*\(|\bcrc64\w*\s*\(",
    "abort_calls": r"\bob_abort\s*\(|\babort\s*\(\s*\)|\bOB_ASSERT\s*\(|\bob_assert\s*\(",
    "Exit": r"\b_Exit\s*\(|\bexit\s*\(",
    "signal": r"\bsignal\s*\(|\bsigaction\s*\(",
    "socket_calls": r"\b(?:socket|bind|listen|accept4?|connect|epoll_create1?|epoll_ctl|epoll_wait|kqueue|kevent)\s*\(",
    "pthread": r"\bpthread_\w+\s*\(",
    "mutable_member": r"\bmutable\b",
    "constexpr": r"\bconstexpr\b",
    "auto_kw": r"\bauto\b",
    "nullptr_or_NULL": r"\bNULL\b|\bnullptr\b",
    "ternary_q": r"\?",
    "switch_kw": r"\bswitch\s*\(",
    "fallthrough_attr": r"\[\[fallthrough\]\]|__attribute__\s*\(\s*\(\s*fallthrough",
    "do_while0": r"\bwhile\s*\(\s*0\s*\)|\bwhile\s*\(\s*false\s*\)",
    "is_contain": r"\bis_contain\s*\(",
    "fma_calls": r"\bfma\s*\(|\bstd::fma\s*\(|\bfmaf\s*\(",
    "printf_family": r"\b(?:snprintf|vsnprintf|sprintf|databuff_printf|BUF_PRINTF)\s*\(",
    "cstring_fns": r"\b(?:strtod|strtol|strtoll|strtoull|atof|atoi|strtof)\s*\(",
    "dtoa": r"\bob_gcvt\w*\s*\(|\bob_fcvt\w*\s*\(|\bdtoa\s*\(",
    "random": r"\b(?:rand|random|rand_r|drand48|ObRandom|std::mt19937\w*)\b\s*\(?",
}
compiled = {k: re.compile(v, re.M) for k, v in PATTERNS.items()}

counts = collections.Counter()
files_hit = collections.Counter()
base_counter = collections.Counter()
goto_files = collections.Counter()
union_files = collections.Counter()
op_kinds = collections.Counter()
multi_inh = 0
total_lines = 0
base_re = re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+(?:\w+\s+)?(\w+)(?:\s+final)?\s*:\s*([^{;]*)", re.M)
op_re = re.compile(r"\boperator\s*(==|!=|<=|>=|<<=|>>=|<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\+\+|--|->\*?|&&|\|\||\(\s*\)|\[\s*\]|[<>+\-*/%&|^!~=,])\s*\(")
for f in files:
    path = os.path.join(ROOT, f)
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    total_lines += raw.count("\n")
    text = strip_comments_and_strings(raw)
    for k, rx in compiled.items():
        c = len(rx.findall(text))
        if c:
            counts[k] += c
            files_hit[k] += 1
    for m in base_re.finditer(text):
        bases = m.group(2)
        depth = 0
        parts = []
        cur = ""
        for ch in bases:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            multi_inh += 1
        for p in parts:
            p = re.sub(r"^(?:public|protected|private|virtual)\s+", "", p)
            p = re.sub(r"^(?:public|protected|private|virtual)\s+", "", p)
            name = re.split(r"[<\s]", p.strip())[0]
            name = name.split("::")[-1]
            if name:
                base_counter[name] += 1
    g = len(compiled["goto"].findall(text))
    if g:
        goto_files[f] = g
    u = len(compiled["union_decl"].findall(text))
    if u:
        union_files[f] = u
    for m in op_re.finditer(text):
        op_kinds[re.sub(r"\s+", "", m.group(1))] += 1

print("files", len(files), "lines", total_lines)
for k in PATTERNS:
    print(f"{k}\t{counts[k]}\tfiles={files_hit[k]}")
print("multiple_inheritance_decls", multi_inh)
print("top bases:")
for name, c in base_counter.most_common(45):
    print(f"  {name}\t{c}")
print("distinct base names", len(base_counter))
print("goto files (top 15):")
for f, c in goto_files.most_common(15):
    print(f"  {f}\t{c}")
print("goto file count", len(goto_files))
print("union files (top 10):")
for f, c in union_files.most_common(10):
    print(f"  {f}\t{c}")
print("operator kinds:")
for k, c in op_kinds.most_common(40):
    print(f"  {k}\t{c}")
