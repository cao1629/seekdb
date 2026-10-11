# Estimate namespace-scope objects of class type (dynamic initialisation candidates) and
# function-local statics, over cached stripped node files.  Heuristic, reported as an estimate.
# usage: python3 -I globals.py
import collections
import os
import re

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
NS_OPEN = re.compile(r'(?:\bnamespace\s*[\w:]*\s*|extern\s+"C"\s*|extern\s+"C\+\+"\s*)$')
# class-typed variable definition at namespace scope
VAR = re.compile(r'^[ \t]*(?:static\s+|extern\s+)?(?:const\s+|constexpr\s+)?(?:thread_local\s+|__thread\s+)?'
                 r'((?:::)?(?:\w+::)*(?:Ob|ob_|std::|lib::|common::)\w*(?:<[^;(){}]*>)?)\s+([A-Za-z_]\w*)\s*'
                 r'(\([^;{}]*\)|\{[^;{}]*\})?\s*;', re.M)
FUNC_DECL_HINT = re.compile(r'\(\s*(?:const\s+)?(?:\w+::)*\w+\s*[&*]|\(\s*(?:int|uint|bool|char|void|int64_t|uint64_t|const)\b')
LOCAL_STATIC = re.compile(r'\bstatic\s+(?!const\b|constexpr\b|inline\b|int\b|bool\b|char\b|void\b|uint\w*\b|int\w*_t\b|double\b|float\b|OB_INLINE\b)((?:\w+::)*Ob\w*(?:<[^;]*?>)?)\s+\w+\s*(?:\([^;]*\))?\s*;')
MACRO_REG = re.compile(r'^[ \t]*(REGISTER_\w+|DEF_\w+|DEFINE_\w+|OB_\w*REGISTER\w*|INIT_\w+)\s*\(', re.M)

ns_vars = []
local_statics = 0
macro_regs = collections.Counter()
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, ROOT)
        t = open(p, encoding='utf-8').read()
        # build a mask of namespace-scope character ranges
        stack = []  # True for namespace braces
        ns_ok = [True] * (len(t) + 1)
        last = 0
        segs = []
        cur_ns = True
        start = 0
        i = 0
        for m in re.finditer(r'[{}]', t):
            j = m.start()
            if t[j] == '{':
                head = t[max(0, j - 120):j]
                is_ns = bool(NS_OPEN.search(head.rstrip() + ' ')) or bool(re.search(r'\bnamespace\b[^;{}]*$', head))
                segs.append((start, j, all(stack)))
                stack.append(is_ns)
                start = j + 1
            else:
                segs.append((start, j, all(stack)))
                if stack:
                    stack.pop()
                start = j + 1
        segs.append((start, len(t), all(stack)))
        for a, b, ok in segs:
            if not ok:
                continue
            chunk = t[a:b]
            for m in VAR.finditer(chunk):
                typ, name, init = m.groups()
                if init and init.startswith('(') and FUNC_DECL_HINT.search(init):
                    continue  # looks like a function declaration
                if re.search(r'\b(?:typedef|using|return|friend|class|struct|enum|template)\b', m.group(0)):
                    continue
                if 'extern' in m.group(0).split(typ)[0]:
                    continue
                ns_vars.append((rel, t.count('\n', 0, a + m.start()) + 1, typ, name))
        local_statics += len(LOCAL_STATIC.findall(t))
        for m in MACRO_REG.finditer(t):
            macro_regs[m.group(1)] += 1

print('namespace-scope class-typed variable definitions (estimate):', len(ns_vars))
byt = collections.Counter(v[2].split('<')[0] for v in ns_vars)
print('  most common types:', byt.most_common(15))
for v in ns_vars[:12]:
    print('   ', f'{v[0]}:{v[1]}', v[2], v[3])
print('function-local `static ObXxx x` (lazy init):', local_statics)
print('top-level registration-like macro invocations:', sum(macro_regs.values()))
print('  ', macro_regs.most_common(15))
