#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import bisect
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SWEEP = os.path.join(ROOT, 'migration', 'inventory', 'sweep', 'tmp-ret.tsv')
NOT_TRANSLATED = os.path.join(ROOT, 'migration', 'not-translated.tsv')
OUT = os.path.join(ROOT, 'migration', 'inventory', 'pre', 'tmp-ret.tsv')

S = r'(?:(?:::)?(?:oceanbase::)?(?:common::)?)OB_SUCCESS'
V = r'[A-Za-z_]\w*'
B = r'(?:(?:common::)?OB_[A-Z0-9_]+|[A-Za-z_]\w*)'

TESTS = [
    (S + r'\s*==\s*(?P<a>%s)' % V, True),
    (r'(?P<a>%s)\s*==\s*' % V + S, True),
    (S + r'\s*!=\s*(?P<a>%s)' % V, False),
    (r'(?P<a>%s)\s*!=\s*' % V + S, False),
    (r'OB_SUCC\s*\(\s*(?P<a>ret)\s*\)', True),
    (r'OB_FAIL\s*\(\s*(?P<a>ret)\s*\)', False),
]
AND_RES = [
    re.compile(r'^\s*(?:return\s+|(?:int\s+)?\*?(?P<t>%s)\s*=\s*)\(?\s*\(?\s*' % V + cond + r'\s*\)?\s*\?\s*'
               + (r'(?P<b>%s)\s*:\s*(?P<a2>%s)' % (B, V) if succ else r'(?P<a2>%s)\s*:\s*(?P<b>%s)' % (V, B))
               + r'\s*\)?\s*;$')
    for cond, succ in TESTS
]
COVER_RE = re.compile(r'^\s*(?:return\s+|\*?(?P<t>%s)\s*=\s*)?COVER_SUCC\s*\(\s*(?P<b>%s)\s*\)\s*;$' % (V, B))
HIDDEN_RE = re.compile(r'\bOB_(?:SUCC|FAIL)\s*\(\s*(?:(?!ret\s*\))%s\s*\)|\(?\s*%s\s*=[^=])' % (V, V))
FIELD_RE = re.compile(r'\b[a-z]\w*_\b(?!\s*\()|->|\w\.\w')
DECL_RES = [
    (re.compile(r'^\s*(?:const\s+)?(?:int|int32_t|int64_t)\s+(?:%s\s*=\s*%s\s*,\s*)?%s\s*=\s*\(?\s*%s\s*\)?\s*;$'
                % (V, S, V, S)), 'int v = OB_SUCCESS;'),
    (re.compile(r'^\s*(?:const\s+)?(?:int|int32_t|int64_t)\s+(?:%s\s*=\s*%s\s*,\s*)?%s\s*;$' % (V, S, V)), 'int v;'),
    (re.compile(r'^\s*INIT_SUCC\s*\(\s*%s\s*\)\s*;$' % V), 'INIT_SUCC(v);'),
    (re.compile(r'[(,]\s*(?:const\s+)?int\s*&?\s*%s\s*(?:[,)]|$)' % V), 'a parameter int v, const int v or int &v'),
]
PARAM_LABEL = DECL_RES[-1][1]
ZERO_RE = re.compile(r'^\s*(?:const\s+)?(?:int|int32_t|int64_t)\s+%s\s*=\s*\(?\s*0\s*\)?\s*;$' % V)
RETRY_RES = [
    (re.compile(r'^\s*while\s*\(\s*OB_TMP_FAIL\s*\('), 'while (OB_TMP_FAIL('),
    (re.compile(r'^\s*\}\s*while\s*\(\s*OB_TMP_FAIL\s*\('), '} while (OB_TMP_FAIL('),
]
NONCODE_RE = re.compile(r'OB_E\s*\(|\bEN_[A-Z0-9_]+\b|snprintf|pthread_\w+\s*\(|\bio_(?:setup|submit|getevents)\s*\('
                        r'|\berrno\b|cmp|compare|\babs\s*\(')
REPLACED_SYMBOLS = {
    'ObCond::wait': 'ObCond::wait becomes a Condvar wait (6.2)',
    'ObCond::timedwait': 'ObCond::timedwait becomes a Condvar wait (6.2)',
    'ObSliceAlloc::alloc': 'ObSliceAlloc::alloc, an OB allocator internal (6.2)',
    'ObVSliceAlloc::alloc': 'ObVSliceAlloc::alloc, an OB allocator internal (6.2)',
}
REPLACED_LINES = {
    ('src/share/cache/ob_kvcache_store.cpp', 1001): 'the hazard-domain reclaim of pop_mb_handle_with_recovery (6.2)',
    ('src/share/cache/ob_kvcache_store.cpp', 1004): 'the hazard-domain reclaim of pop_mb_handle_with_recovery (6.2)',
    ('src/oblib/lib/utility/ob_macro_utils.h', 656): 'the #define of OB_TMP_FAIL (6.2)',
    ('src/oblib/lib/utility/ob_macro_utils.h', 657): 'the #define of COVER_SUCC (6.2)',
    ('src/oblib/lib/utility/ob_smart_var.h', 171): 'the __SMART_VAR body (6.2)',
    ('src/oblib/lib/utility/ob_smart_var.h', 172): 'the __SMART_VAR body (6.2)',
    ('src/rootserver/ob_local_management_service.cpp', 2318): 'an explicit unlock, the drop of its guard (6.2)',
    ('src/rootserver/ob_local_management_service.cpp', 2319): 'an explicit unlock, the drop of its guard (6.2)',
    ('src/storage/blocksstable/ob_block_manager.cpp', 1769): 'an explicit unlock, the drop of its guard (6.2)',
}
FAMILY = ('tmp_ret', 'temp_ret', 'tmp_ret_code', 'tmp_ret_2')
PROPOSABLE = ('NOT_TRANSLATED', 'REPLACED', 'MERGE_AND', 'MERGE_AS_WRITTEN', 'RETRY', 'DECLARATION')

LIBC = {'close', 'read', 'write', 'open', 'pread', 'pwrite', 'fsync', 'fdatasync', 'fstat', 'stat', 'lstat', 'unlink',
        'rename', 'mkdir', 'rmdir', 'ftruncate', 'fallocate', 'lseek', 'ioctl', 'usleep', 'sleep', 'system', 'snprintf',
        'vsnprintf', 'sprintf', 'strcmp', 'strncmp', 'strcasecmp', 'strncasecmp', 'memcmp', 'atoi', 'atol', 'atoll',
        'strtol', 'strtoll', 'strtoul', 'strtoull', 'getrlimit', 'setrlimit', 'gettimeofday', 'clock_gettime',
        'access', 'dup', 'dup2', 'pipe', 'poll', 'select', 'socket', 'bind', 'listen', 'accept', 'connect', 'send',
        'recv', 'syscall', 'prctl', 'fcntl', 'opendir', 'closedir', 'readdir', 'statfs', 'fstatfs', 'posix_memalign',
        'getaddrinfo', 'inet_pton', 'abs', 'labs', 'llabs', 'rand', 'random'}
CODE_PRINTERS = {'databuff_printf', 'databuff_vprintf', 'logdata_printf', 'logdata_vprintf', 'BUF_PRINTF',
                 'databuff_print_obj', 'databuff_print_json', 'databuff_print_kv'}
LEX_RE = re.compile(
    r'''(?=[/"'uULR])(?://[^\n]*|/\*.*?\*/|(?<!\w)(?:u8|u|U|L)?R"([^()\\\s"]{0,16})\(.*?\)\1"|'''
    r'''(?:(?<!\w)(?:u8|u|U|L))?"(?:[^"\\\n]|\\.)*"|(?<![\w'])'(?:[^'\\\n]|\\.)*')''', re.S)
CODE_CONST_RE = re.compile(r'(?:(?:::)?(?:oceanbase::)?(?:common::)?)OB_[A-Z0-9_]+$')
CHAIN = r'\s*(?:::)?[\w~]+(?:\s*(?:::|\.|->)\s*[\w~]+|\s*\(\s*\)|\s*\[\s*\])*'
LAMBDA_HEAD_RE = re.compile(r'\]\s*(?:\([^()]*\))?\s*(?:mutable\s*)?(?:->\s*[\w:<>*&\s]+?)?\s*$')
KEYWORDS = {'ret', 'return', 'int', 'if', 'else', 'const', 'while', 'do', 'void', 'true', 'false', 'nullptr', 'NULL'}


def read_tsv(path):
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines[0].split('\t'), [(n + 1, line.split('\t')) for n, line in enumerate(lines) if n > 0]


def not_translated_spans():
    spans = collections.defaultdict(list)
    for tsv_line, row in read_tsv(NOT_TRANSLATED)[1]:
        for entry in row[2].split(','):
            entry = entry.strip()
            m = re.match(r'(.*):(\d+)-(\d+)$', entry)
            if m:
                spans[m.group(1)].append((int(m.group(2)), int(m.group(3)), tsv_line, row[1]))
            elif entry:
                spans[entry].append((0, 10 ** 9, tsv_line, row[1]))
    return spans


def clean(text):
    return re.sub(r'/\*.*?\*/|//.*$', '', text).rstrip().rstrip('\\').rstrip()


def row_variables(construct, text):
    m = re.match(r'(?:declare|assign) (\w+)', construct)
    if m:
        return [m.group(1)]
    if construct.startswith('OB_TMP_FAIL'):
        return ['tmp_ret']
    m = re.match(r'merge (\w+) into', construct)
    if m and not m.group(1).startswith('OB_'):
        return [m.group(1)]
    return [v for v in FAMILY if re.search(r'\b%s\b' % v, text)]


def and_match(t, own=None):
    m = COVER_RE.match(t)
    if m:
        if own is None:
            return 'COVER_SUCC(%s)' % m.group('b'), None, m.group('b')
        return None
    for r in AND_RES:
        m = r.match(t)
        if m and m.group('a') == m.group('a2') and (own is None or m.group('t') == own):
            return 'a = %s, b = %s' % (m.group('a'), m.group('b')), m.group('a'), m.group('b')
    return None


def hidden_merge(t):
    return bool(HIDDEN_RE.search(t) and ('?' in t or '=' in t.split('OB_', 1)[1]))


def propose(row, nt):
    f, line, sym, con, text = row[0], int(row[1]), row[2], row[3], row[4]
    t = clean(text)
    var = row_variables(con, text)
    own = var[0] if con.startswith('assign') and var else None
    for a, b, tsv_line, reason in nt.get(f, []):
        if a <= line <= b:
            return 'NOT_TRANSLATED', 'step 1: not-translated.tsv:%d (%s)' % (tsv_line, reason), []
    if f.startswith('src/oblib/lib/lock/'):
        return 'REPLACED', 'step 2: a lock class under src/oblib/lib/lock/ (6.2)', []
    if sym in REPLACED_SYMBOLS:
        return 'REPLACED', 'step 2: ' + REPLACED_SYMBOLS[sym], []
    if 'global_hazard_station_' in text:
        return 'REPLACED', 'step 2: global_hazard_station_, hazard reclamation (6.2)', []
    if (f, line) in REPLACED_LINES:
        return 'REPLACED', 'step 2: ' + REPLACED_LINES[(f, line)], []
    if 'OB_ALLOCATE_MEMORY_FAILED' in text:
        return 'READ', 'read: sets, merges or tests -4013 (section 7 comes first)', []
    if 'merge' in con or hidden_merge(t) or (own and and_match(t, own)):
        if FIELD_RE.search(re.sub(r'\bOB_\w+', '', t)):
            return 'READ', 'read: a merge through a field (step 3 needs its writers)', []
        operands = list(var)
        for name in re.findall(r'(?<![\w.>:])([a-z_]\w*)\b(?!\s*\()', t):
            if name not in KEYWORDS and name not in operands:
                operands.append(name)
        m = HIDDEN_RE.search(t)
        if m:
            return 'MERGE_AS_WRITTEN', 'step 4: hidden merge %s' % m.group(0).rstrip('=').rstrip(), operands
        shape = and_match(t)
        if shape:
            return 'MERGE_AND', 'step 4: AND-shaped, %s' % shape[0], operands
        return 'MERGE_AS_WRITTEN', 'step 4: a merge line, not AND-shaped', operands
    for r, label in RETRY_RES:
        if r.match(t):
            return 'RETRY', 'step 6: ' + label, ['tmp_ret']
    if con.startswith('declare'):
        for r, label in DECL_RES:
            if r.search(t):
                return 'DECLARATION', 'step 5: ' + label, var
        if ZERO_RE.match(t):
            return 'READ', 'read: a declaration initialized to 0 (step 3)', []
        return 'READ', 'read: a declaration with a code', []
    if con.startswith('OB_TMP_FAIL'):
        return 'READ', 'read: OB_TMP_FAIL', []
    return 'READ', 'read: an assignment', []


def blank_match(m):
    s = m.group(0)
    if s[0] == '/':
        return re.sub(r'[^\n]', ' ', s)
    return s[0] + re.sub(r'[^\n]', ' ', s[1:-1]) + s[-1]


class Source:
    def __init__(self, path):
        with open(os.path.join(ROOT, path), encoding='utf-8', errors='replace') as fh:
            raw = fh.read()
        self.raw = raw.split('\n')
        self.code = LEX_RE.sub(blank_match, raw)
        self.starts = [0]
        for text in self.code.split('\n'):
            self.starts.append(self.starts[-1] + len(text) + 1)

    def line_of(self, pos):
        return bisect.bisect_right(self.starts, pos)

    def pos(self, line):
        return self.starts[line - 1]


SOURCES = {}


def source(path):
    if path not in SOURCES:
        SOURCES[path] = Source(path)
    return SOURCES[path]


def closing(code, i, open_ch, close_ch):
    depth = 0
    while i < len(code):
        if code[i] == open_ch:
            depth += 1
        elif code[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(code) - 1


def macro_last_line(src, line):
    while line <= len(src.raw) and src.raw[line - 1].rstrip().endswith('\\'):
        line += 1
    return line


def scope(src, line, var, param, macro):
    code = src.code
    m = re.compile(r'\b%s\b' % re.escape(var)).search(code, src.pos(line))
    if not m:
        return None
    start = m.start()
    depth, i = 0, start
    if param:
        while i < len(code):
            if code[i] == '(':
                depth += 1
            elif code[i] == ')':
                depth -= 1
            elif depth < 0 and code[i] in '{;':
                break
            i += 1
        if i >= len(code) or code[i] == ';':
            return None
        return start, closing(code, i, '{', '}')
    while i < len(code):
        if code[i] == '{':
            depth += 1
        elif code[i] == '}':
            depth -= 1
            if depth < 0:
                break
        i += 1
    if macro:
        i = min(i, src.starts[macro_last_line(src, line)] - 1)
    return start, i


def expr_end(code, i, stop):
    depth = 0
    while i < stop:
        c = code[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return i
            depth -= 1
        elif c in ';,' and depth == 0:
            return i
        i += 1
    return stop


def top_level(e):
    out, depth = [], 0
    for c in e:
        if c in '([{':
            out.append(c if depth == 0 else ' ')
            depth += 1
        elif c in ')]}':
            depth -= 1
            out.append(c if depth == 0 else ' ')
        else:
            out.append(c if depth == 0 else ' ')
    return ''.join(out)


def strip_parens(e):
    e = e.strip()
    while e.startswith('(') and closing(e, 0, '(', ')') == len(e) - 1:
        e = e[1:-1].strip()
    return e


def value_kind(e):
    e = strip_parens(re.sub(r'\s+', ' ', e.replace('\\', ' ')))
    before = None
    while before != e:
        before = e
        e = re.sub(r'(\w)\s*<[^<>()]*>', r'\1', e)
    e = strip_parens(re.sub(r'\boperator\s*\(\s*\)', 'operator_call', e))
    if not e or CODE_CONST_RE.match(e) or e == '0':
        return None
    if re.search(r'OB_E\s*\(|\bEN_[A-Z0-9_]+\b|\bERRSIM', e):
        return 'a tracepoint value'
    top = top_level(e)
    m = re.search(r'(?<![=!<>])=(?!=)', top)
    if m:
        return value_kind(e[m.end():])
    q = top.find('?')
    if q >= 0:
        c = top.find(':', q)
        while c >= 0 and (top[c - 1:c] == ':' or top[c + 1:c + 2] == ':'):
            c = top.find(':', c + 2)
        if c > q:
            return value_kind(e[q + 1:c]) or value_kind(e[c + 1:])
        return 'a conditional'
    if re.match(r'\(\s*(?:const\s+)?(?:u?int\w*|long|short|unsigned|signed|char|bool)\s*\)', e):
        return 'a cast'
    if re.search(r'[-+*/%<>|&^~!]', top.replace('->', '  ').replace('::', '  ')):
        return 'arithmetic or a comparison'
    if re.match(r'-?\d', e):
        return 'a number'
    if re.match(r'-?[A-Z][A-Z0-9_]*$', e):
        return 'a constant that is not a code'
    if re.match(r'[a-z_]\w*$', e):
        return None
    if re.fullmatch(CHAIN + r'\s*\(\s*\)\s*', top):
        name = re.search(r'([\w~]+)\s*\(\s*\)\s*$', top).group(1)
        member = bool(re.search(r'(?:\.|->)\s*[\w~]+\s*\(\s*\)\s*$', top))
        args = e[top.rindex('(') + 1:top.rindex(')')].strip()
        if name in ('static_cast', 'reinterpret_cast', 'const_cast', 'dynamic_cast'):
            return 'a cast'
        if not member and name in LIBC:
            return 'the OS or libc call %s' % name
        if name in CODE_PRINTERS:
            return None
        if re.search(r'pthread_|^io_(?:setup|submit|getevents|destroy|cancel)$|printf$', name):
            return 'the call %s' % name
        if re.search(r'cmp|compare', name, re.I):
            return 'a comparison %s' % name
        if not args and re.search(r'(?:count|size|len|length|num|cnt)$', name, re.I):
            return 'a count %s' % name
        return None
    if re.fullmatch(CHAIN + r'\s*', top):
        return 'a field or element'
    return 'an expression'


def in_lambda(code, start, p):
    stack = []
    for i in range(start, p):
        if code[i] == '{':
            stack.append(i)
        elif code[i] == '}' and stack:
            stack.pop()
    return any(LAMBDA_HEAD_RE.search(code[max(0, b - 200):b]) for b in stack)


def operand_is_code(tok):
    tok = tok.strip().lstrip('(').strip().replace('->', '.')
    return bool(CODE_CONST_RE.match(tok) or tok == '0'
                or re.match(r'[a-z_]\w*(?:\s*\.\s*\w+|\s*\(\s*\))*$', tok))


def argument_of(code, start, s, e):
    if code[e:e + 60].lstrip()[:1] not in (',', ')'):
        return None
    depth, j = 0, s - 1
    while j >= start:
        c = code[j]
        if c in ')]':
            depth += 1
        elif c in '([':
            if depth == 0:
                break
            depth -= 1
        elif c in ';{}' and depth == 0:
            return None
        j -= 1
    if j < start or code[j] != '(' or not re.search(r'(?:^|,)\s*$', top_level(code[j + 1:s])):
        return None
    m = re.search(r'([\w~]+)\s*(?:<[^<>()]*>)?\s*$', code[max(0, j - 100):j])
    return m.group(1) if m else None


def use_problem(code, start, s, e):
    prev = code[max(start, s - 60):s].rstrip()
    nxt = code[e:e + 60].lstrip()
    callee = argument_of(code, start, s, e)
    if callee and re.search(r'cmp|compare|func_?$', callee, re.I):
        return 'passed to %s, which can write a comparison result into it' % callee
    if re.match(r'(?:\+\+|--|[-+*/%^|&]=|<<=|>>=)', nxt) or re.search(r'(?:\+\+|--)$', prev):
        return 'changed as a number'
    if re.match(r'(?:<=|>=|<(?!<)|>(?!>)|<<|>>)', nxt) or re.search(r'(?:<=|>=|(?<![-<])<|(?<![->])>)$', prev):
        return 'compared as a number'
    if re.match(r'[-+*/%](?![->=])', nxt) or re.search(r'(?:[+*/%]|(?<!-)-(?!>))$', prev):
        return 'used in arithmetic'
    if re.search(r'&\s*\w+\s*=\s*$', prev):
        return 'aliased by a reference (section 7)'
    if re.search(r'(?<![&])&$', prev):
        before = prev[:-1].rstrip()
        if before and (before[-1].isalnum() or before[-1] in '_)]'):
            return 'used in arithmetic'
        return 'its address taken (section 7)'
    if re.search(r'(?<![|])\|$|\^$|~$|\[$', prev) or re.match(r'(?:\|(?!\|)|\^|\[)', nxt):
        return 'used as a number'
    if re.search(r'\b(?:abs|labs|llabs)\s*\($|_cast\s*<[^<>]*>\s*\($', prev):
        return 'used as a number'
    m = re.match(r'(?:==|!=)\s*(\(*\s*-?[\w:.>\-]+(?:\s*\(\s*\))?)', nxt)
    if m and not operand_is_code(m.group(1)):
        return 'compared with %s' % m.group(1).strip()
    m = re.search(r'(-?[\w:.]+(?:->\w+)*|\))\s*(?:==|!=)\s*\(?\s*$', prev)
    if m and not operand_is_code(m.group(1)):
        return 'compared with %s' % m.group(1).strip()
    return None


def scan(src, line, var, param, macro):
    sc = scope(src, line, var, param, macro)
    if sc is None:
        return []
    start, end = sc
    code = src.code
    hits = []
    word = re.compile(r'\b%s\b' % re.escape(var))
    writers = [re.compile(r'\b(?:OB_TMP_FAIL|CLICK_TMP_FAIL)\s*\(')] if var == 'tmp_ret' else []
    for pat in [word] + writers:
        for m in pat.finditer(code, start, end):
            s, e = m.start(), m.end()
            ln = src.line_of(s)
            if s > start and in_lambda(code, start, s):
                hits.append((ln, 'used inside a lambda (section 7)'))
                continue
            if pat is not word:
                kind = value_kind(code[e:expr_end(code, e, end)])
                if kind:
                    hits.append((ln, 'written from %s' % kind))
                continue
            rest = code[e:end]
            a = re.match(r'\s*=(?!=)', rest)
            if a:
                kind = value_kind(code[e + a.end():expr_end(code, e + a.end(), end)])
                if kind:
                    hits.append((ln, 'written from %s' % kind))
                continue
            if s == start:
                continue
            problem = use_problem(code, start, s, e)
            if problem:
                hits.append((ln, problem))
    return sorted(set(hits))


def is_parameter(text, var):
    return bool(re.search(r'[(,]\s*(?:const\s+)?(?:int|int32_t|int64_t)\s*&?\s*%s\s*(?:[,)=]|$)' % re.escape(var), text))


def find_declaration(src, var, line, lowest):
    local = re.compile(r'(?:^|[;{(,])\s*(?:const\s+)?(?:int|int32_t|int64_t)\s*&?\s*%s\b(?!\s*\()' % re.escape(var))
    init = re.compile(r'\bINIT_SUCC\s*\(\s*%s\s*\)' % re.escape(var))
    for n in range(line, max(lowest, 1) - 1, -1):
        text = src.code[src.starts[n - 1]:src.starts[n] - 1]
        m = local.search(text) or init.search(text)
        if not m:
            continue
        for param in ((True, False) if m.group(0).lstrip()[:1] in '(,' else (False,)):
            sc = scope(src, n, var, param, False)
            if sc and sc[0] <= src.pos(line) <= sc[1]:
                return n, param
    return None


def do_while_reassigns(src, line):
    code = src.code
    b = code.find('}', src.pos(line))
    depth, i = 0, b
    while i >= 0:
        if code[i] == '}':
            depth += 1
        elif code[i] == '{':
            depth -= 1
            if depth == 0:
                break
        i -= 1
    return bool(re.search(r'\btmp_ret\s*=(?!=)|\b(?:OB_TMP_FAIL|CLICK_TMP_FAIL)\s*\(', code[i:b]))


def step3_problem(row, proposed, pattern, variables, declarations):
    f, line, sym = row[0], int(row[1]), row[2]
    src = source(f)
    macro = sym.startswith('#define')
    if proposed == 'RETRY' and pattern.endswith('} while (OB_TMP_FAIL(') and not do_while_reassigns(src, line):
        return 'the do-while body does not produce the code again (6.7)'
    for var in variables:
        if proposed == 'DECLARATION':
            found = (line, pattern.endswith(PARAM_LABEL))
        else:
            rows = [d for d in declarations.get((f, sym, var), []) if d[0] <= line]
            if rows:
                found = (rows[-1][0], is_parameter(clean(rows[-1][1]), var))
            else:
                found = find_declaration(src, var, line, line - 3000)
                if found is None:
                    if var in row_variables(row[3], row[4]):
                        return 'no declaration of %s in view' % var
                    continue
        for ln, what in scan(src, found[0], var, found[1], macro):
            return '%s %s at %s:%d' % (var, what, f, ln)
    return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    header, rows = read_tsv(SWEEP)
    nt = not_translated_spans()
    risky = collections.defaultdict(list)
    declarations = collections.defaultdict(list)
    for tsv_line, r in rows:
        if 'merge' not in r[3] and NONCODE_RE.search(r[4]):
            for v in row_variables(r[3], r[4]):
                risky[(r[0], r[2], v)].append(tsv_line)
        m = re.match(r'declare (\w+)', r[3])
        if m:
            declarations[(r[0], r[2], m.group(1))].append((int(r[1]), r[4]))
    result = []
    for tsv_line, r in rows:
        proposed, pattern, variables = propose(r, nt)
        if proposed in PROPOSABLE[2:]:
            hits = sorted({x for v in row_variables(r[3], r[4]) for x in risky.get((r[0], r[2], v), [])})
            if hits:
                proposed, pattern = 'READ', 'read: step 3, the function also writes the variable from a tracepoint, an OS or printf call or a comparison (tmp-ret.tsv:%s; the text alone gives %s)' % (
                    ', '.join(str(x) for x in hits), proposed)
            else:
                problem = step3_problem(r, proposed, pattern, variables, declarations)
                if problem:
                    proposed, pattern = 'READ', 'read: step 3 or section 7, %s (the text alone gives %s)' % (problem, proposed)
        result.append(r + [proposed, pattern])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write('\t'.join(header + ['proposed', 'pattern']) + '\n')
        for r in result:
            fh.write('\t'.join(r) + '\n')
    counts = collections.Counter(r[-2] for r in result)
    for value in PROPOSABLE + ('READ',):
        print('%s\t%d' % (value, counts.get(value, 0)))
    print('rows\t%d' % len(result))
    print('READ share\t%.1f%%' % (100.0 * counts.get('READ', 0) / len(result)))


if __name__ == '__main__':
    main()
