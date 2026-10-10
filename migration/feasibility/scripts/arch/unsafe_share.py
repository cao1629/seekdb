# Per-module share of files/lines that contain constructs a structure-preserving Rust port could not
# express in safe Rust. Runs over the comment/string-stripped mirror (generated files already excluded).
# Usage: python3 -I unsafe_share.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
TIER_A = {  # needs `unsafe` in a 1:1 translation
    'placement_new': r'\bnew\s*\((?!\s*std::nothrow)',
    'ob_new_macro': r'\b(OB_NEW\w*|MTL_NEW\w*|OB_DELETE\w*|MTL_DELETE\w*)\s*\(',
    'explicit_dtor': r'(->|\.)~\w+\s*\(\s*\)',
    'reinterpret_cast': r'\breinterpret_cast\s*<',
    'c_style_ptr_cast': r'\(\s*(const\s+)?[A-Za-z_][\w:]*\s*\*\s*\)\s*[\w(&]',
    'raw_alloc_call': r'(\.|->)alloc(_aligned|_align)?\s*\(',
    'mem_copy_set': r'(?<![\w.>])(memcpy|MEMCPY|memmove|MEMMOVE|memset|MEMSET)\s*\(',
    'atomics': r'\b(ATOMIC_[A-Z_]+|__sync_\w+|__atomic_\w+)\s*\(',
    'flex_array': r'\w+\s+\w+\s*\[\s*0\s*\]\s*;',
    'asm_simd': r'\b(asm|__asm__)\s*(volatile|__volatile__)?\s*\(|\b_mm(256|512)?_\w+\s*\(|\bv(ld|st)[1-4]q?_\w+\s*\(',
    'setjmp': r'\b(sig)?(set|long)jmp\s*\(',
}
TIER_B = {  # safe Rust only after an ownership redesign (arena+index, Rc/RefCell, Arc, Pin)
    'raw_ptr_member': r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*\*+\s*(const\s+)?\w+_\s*(\[[^\]]*\])?\s*;',
    'ptr_ref_slot': r'\b[A-Z]\w*\s*\*\s*&\s*\w+',
    'intrusive': r'\b(ObDLinkBase|ObDList|ObDLinkNode|ObLink|ObDLink|ObLightHashLink|ObSpLinkQueue|ObLinkQueue|ObLinkHashMap|LinkHashNode|LinkHashValue)\b',
    'manual_ref': r'\b(inc_ref\w*|dec_ref\w*|acquire_ctx_ref|release_ctx_ref|inc_ref_cnt|dec_ref_cnt)\s*\(',
}
ca = {k: re.compile(v) for k, v in TIER_A.items()}
cb = {k: re.compile(v) for k, v in TIER_B.items()}
mod_files = collections.Counter(); mod_lines = collections.Counter()
a_files = collections.Counter(); a_lines = collections.Counter()
ab_files = collections.Counter(); ab_lines = collections.Counter()
a_sites = collections.Counter(); b_sites = collections.Counter()
cat_sites = collections.Counter(); cat_files = collections.Counter()
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, os.path.join(root, 'src'))
        mod = rel.split(os.sep)[0]
        s = open(p, encoding='utf-8', errors='replace').read()
        nl = s.count('\n') + 1
        mod_files[mod] += 1; mod_lines[mod] += nl
        ha = hb = False
        for k, r in ca.items():
            n = len(r.findall(s))
            if n:
                ha = True; a_sites[mod] += n; cat_sites[k] += n; cat_files[k] += 1
        for k, r in cb.items():
            n = len(r.findall(s))
            if n:
                hb = True; b_sites[mod] += n; cat_sites[k] += n; cat_files[k] += 1
        if ha:
            a_files[mod] += 1; a_lines[mod] += nl
        if ha or hb:
            ab_files[mod] += 1; ab_lines[mod] += nl
print('%-11s %7s %9s | %-22s | %-22s | %8s %8s' % ('module', 'files', 'lines', 'TierA files/lines %', 'TierA|B files/lines %', 'A/KLOC', 'B/KLOC'))
T = collections.Counter()
for m in sorted(mod_lines, key=lambda x: -mod_lines[x]):
    print('%-11s %7d %9d | %5.1f%% / %5.1f%%        | %5.1f%% / %5.1f%%        | %8.1f %8.1f' % (
        m, mod_files[m], mod_lines[m], 100.0 * a_files[m] / mod_files[m], 100.0 * a_lines[m] / mod_lines[m],
        100.0 * ab_files[m] / mod_files[m], 100.0 * ab_lines[m] / mod_lines[m],
        1000.0 * a_sites[m] / mod_lines[m], 1000.0 * b_sites[m] / mod_lines[m]))
    for c, v in (('f', mod_files[m]), ('l', mod_lines[m]), ('af', a_files[m]), ('al', a_lines[m]), ('abf', ab_files[m]), ('abl', ab_lines[m]), ('as', a_sites[m]), ('bs', b_sites[m])):
        T[c] += v
print('%-11s %7d %9d | %5.1f%% / %5.1f%%        | %5.1f%% / %5.1f%%        | %8.1f %8.1f' % (
    'TOTAL', T['f'], T['l'], 100.0 * T['af'] / T['f'], 100.0 * T['al'] / T['l'], 100.0 * T['abf'] / T['f'], 100.0 * T['abl'] / T['l'],
    1000.0 * T['as'] / T['l'], 1000.0 * T['bs'] / T['l']))
print('Tier A sites:', T['as'], ' Tier B sites:', T['bs'])
print('per-category sites/files:')
for k in list(TIER_A) + list(TIER_B):
    print('   %-18s sites=%-7d files=%d' % (k, cat_sites[k], cat_files[k]))
