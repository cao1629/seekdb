# Function-level estimate: share of out-of-line function bodies (in .cpp/.c/.cc/.ipp files of the
# stripped mirror) that contain a raw-pointer dereference ('->') or a Tier-A construct (see
# unsafe_share.py). In a structure-preserving port that keeps raw pointers, such a body needs `unsafe`.
# Namespace / extern blocks are treated as transparent; a function body is a '{' at depth 0 whose
# preceding non-space char is ')' or a trailing qualifier (const/override/final/noexcept).
# Usage: python3 -I func_share.py <mirror_root>
import os, re, sys, collections
root = sys.argv[1]
TIER_A = re.compile('|'.join([
    r'\bnew\s*\((?!\s*std::nothrow)', r'\b(OB_NEW\w*|MTL_NEW\w*|OB_DELETE\w*|MTL_DELETE\w*)\s*\(',
    r'(->|\.)~\w+\s*\(\s*\)', r'\breinterpret_cast\s*<', r'\(\s*(const\s+)?[A-Za-z_][\w:]*\s*\*\s*\)\s*[\w(&]',
    r'(\.|->)alloc(_aligned|_align)?\s*\(', r'(?<![\w.>])(memcpy|MEMCPY|memmove|MEMMOVE|memset|MEMSET)\s*\(',
    r'\b(ATOMIC_[A-Z_]+|__sync_\w+|__atomic_\w+)\s*\(', r'\b(asm|__asm__)\s*(volatile|__volatile__)?\s*\(',
    r'\b_mm(256|512)?_\w+\s*\(', r'\b(sig)?(set|long)jmp\s*\(']))
ARROW = re.compile(r'->')
NS = re.compile(r'(\bnamespace\s*[\w:]*\s*|\bextern\s*""\s*)$')
QUAL = re.compile(r'(\)|\bconst|\boverride|\bfinal|\bnoexcept|\bvolatile)\s*$')
stats = collections.defaultdict(lambda: collections.Counter())
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        if not f.endswith(('.cpp', '.c', '.cc', '.ipp')):
            continue
        p = os.path.join(dp, f)
        mod = os.path.relpath(p, os.path.join(root, 'src')).split(os.sep)[0]
        s = open(p, encoding='utf-8', errors='replace').read()
        i, n, depth = 0, len(s), 0
        stack = []  # True for transparent braces
        while i < n:
            c = s[i]
            if c == '{':
                pre = s[max(0, i - 80):i]
                if NS.search(pre):
                    stack.append(True)
                    i += 1
                    continue
                if depth == 0 and QUAL.search(pre):
                    # function body: find matching brace
                    d, j = 0, i
                    while j < n:
                        if s[j] == '{':
                            d += 1
                        elif s[j] == '}':
                            d -= 1
                            if d == 0:
                                break
                        j += 1
                    body = s[i:j + 1]
                    nl = body.count('\n') + 1
                    a = bool(TIER_A.search(body)); r = bool(ARROW.search(body))
                    st = stats[mod]
                    st['funcs'] += 1; st['lines'] += nl
                    if a:
                        st['a_funcs'] += 1; st['a_lines'] += nl
                    if a or r:
                        st['ar_funcs'] += 1; st['ar_lines'] += nl
                    i = j + 1
                    continue
                stack.append(False); depth += 1
            elif c == '}':
                if stack:
                    t = stack.pop()
                    if not t:
                        depth -= 1
            i += 1
T = collections.Counter()
print('%-11s %7s %9s | %-26s | %-26s' % ('module', 'funcs', 'body_lines', 'TierA funcs% / lines%', 'TierA or -> funcs% / lines%'))
for m in sorted(stats, key=lambda x: -stats[x]['lines']):
    st = stats[m]
    T.update(st)
    if st['funcs'] == 0:
        continue
    print('%-11s %7d %9d | %6.1f%% / %6.1f%%          | %6.1f%% / %6.1f%%' % (m, st['funcs'], st['lines'],
          100.0 * st['a_funcs'] / st['funcs'], 100.0 * st['a_lines'] / st['lines'],
          100.0 * st['ar_funcs'] / st['funcs'], 100.0 * st['ar_lines'] / st['lines']))
print('%-11s %7d %9d | %6.1f%% / %6.1f%%          | %6.1f%% / %6.1f%%' % ('TOTAL', T['funcs'], T['lines'],
      100.0 * T['a_funcs'] / T['funcs'], 100.0 * T['a_lines'] / T['lines'],
      100.0 * T['ar_funcs'] / T['funcs'], 100.0 * T['ar_lines'] / T['lines']))
