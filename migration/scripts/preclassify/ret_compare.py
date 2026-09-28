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
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SWEEP = os.path.join(ROOT, 'migration', 'inventory', 'sweep', 'ret-compare.tsv')
NOT_TRANSLATED = os.path.join(ROOT, 'migration', 'not-translated.tsv')
OUT = os.path.join(ROOT, 'migration', 'inventory', 'pre', 'ret-compare.tsv')

GRAMMAR = 'src/sql/parser/sql_parser_mysql_mode.y'
DEFINE_H = 'src/share/ob_define.h'
DEFINE_H_SPAN = (71, 265)
OOM_CODE = 'OB_ALLOCATE_MEMORY_FAILED'
INIT_CODES = ('OB_NOT_INIT', 'OB_INIT_TWICE')
ITER_CODES = ('OB_ITER_END', 'OB_ITER_STOP')
LOOKUP_CODES = frozenset(('OB_HASH_NOT_EXIST', 'OB_HASH_EXIST', 'OB_ENTRY_NOT_EXIST', 'OB_ENTRY_EXIST',
                          'OB_SEARCH_NOT_FOUND', 'OB_EMPTY_RESULT', 'OB_READ_NOTHING',
                          'OB_ERR_PRIMARY_KEY_DUPLICATE', 'OB_ERR_NULL_VALUE'))
LOOKUP_SUFFIX_RE = re.compile(r'_(?:NOT_EXISTS?|NOT_FOUND|EXISTS?)$')
BUFFER_CODES = frozenset(('OB_SIZE_OVERFLOW', 'OB_BUF_NOT_ENOUGH', 'OB_HASH_FULL'))
PROPOSABLE = ('NOT_TRANSLATED', 'CODE_PREDICATE', 'OUT_OF_MEMORY', 'INIT_STATE', 'RENAMED', 'END_OF_DATA')

LEX_RE = re.compile(
    r'''(?=[/"'uULR])(?://[^\n]*|/\*.*?\*/|(?<!\w)(?:u8|u|U|L)?R"([^()\\\s"]{0,16})\(.*?\)\1"|'''
    r'''(?:(?<!\w)(?:u8|u|U|L))?"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')''', re.S)
DIRECTIVE_RE = re.compile(r'^[ \t]*#[ \t]*(\w*)')
DEFINE_HEAD_RE = re.compile(r'^[ \t]*#[ \t]*define[ \t]+\w+(?:\([^)]*\))?')
DELIM_RE = re.compile(r'[{}();]')
ACCESS_RE = re.compile(r'^(?:\s*(?:public|private|protected)\s*:(?!:))+')
QUAL = r'(?:::\s*)?(?:oceanbase\s*::\s*)?(?:common\s*::\s*)?'
CODE_OPERAND_RE = re.compile(QUAL + r'(OB_[A-Z0-9_]+)')
LIKELY_RE = re.compile(r'OB_(?:UN)?LIKELY\s*\(')
IF_LINE_RE = re.compile(r'(\}\s*else\s+)?if\s*\(')
LABEL_RE = re.compile(r'^(?:(?:case\b[^:]*|default|[A-Za-z_]\w*)\s*:(?!:)\s*)+')
LOOP_WORD_RE = re.compile(r'\b(?:for|while)\s*\(|\bdo\b')
MACRO_HEAD_RE = re.compile(r'^[A-Z_][A-Z0-9_]*\s*\(')
LOG_NAME_RE = re.compile(
    r'_?(?:[A-Z][A-Z0-9]*_)*LOG(?:_RET)?|_?F?LOG_(?:WARN|INFO|ERROR|DEBUG|TRACE)(?:_RET)?|'
    r'LOG_DBA_(?:WARN|ERROR|INFO)(?:_V2)?|LOG_DBA_FORCE_PRINT|LOG_WARN_IGNORE_[A-Z_]+|MDS_LOG_[A-Z]+|'
    r'TX_REPLAY_LOG|CLOG_LOG_LIMIT')
NOT_LOG_NAMES = frozenset(('ALLOW_NEXT_LOG', 'CONTROL_EVENT_ADD_LOG', 'THROTTLE_CONFIG_LOG'))
TAG_RE = re.compile(r'(?:\s*\[(?:not built|data|generated|vendored)\])+$')
PART_RE = re.compile(r'^(?P<h>.*) (?P<op>==|!=) (?P<code>OB_[A-Z0-9_]+)$')
CASE_PART_RE = re.compile(r'^switch \((?P<h>.*)\) case (?P<code>OB_[A-Z0-9_]+)$')
TOKEN_RE = re.compile(
    r'\s*(?:(?P<logic>\|\||&&)|(?P<cmp>==|!=)|(?P<likely>OB_(?:UN)?LIKELY\s*\()|(?P<lp>\()|(?P<rp>\))|'
    r'(?P<id>(?:::\s*)?[A-Za-z_]\w*(?:\s*(?:::|\.|->)\s*[A-Za-z_]\w*)*)(?P<call>\s*\()?)')


def read_tsv(path):
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines[0].split('\t'), [(n + 1, line.split('\t')) for n, line in enumerate(lines) if n > 0]


def load_catalog():
    names = {'OB_SUCCESS'}
    for rel, pattern in (('src/oblib/lib/ob_errno.h', r'constexpr\s+int\s+(OB_\w+)\s*=\s*-\d+'),
                         ('src/share/ob_errno.h', r'constexpr\s+int\s+(OB_\w+)\s*=\s*-\d+'),
                         ('src/share/ob_errno.def', r'^DEFINE_\w+\(\s*(OB_\w+)'),
                         ('src/sql/parser/parse_define.h', r'\bconst\s+int(?:32_t)?\s+(OB_PARSER_\w+)\s*=\s*-\d+')):
        path = os.path.join(ROOT, rel)
        if os.path.exists(path):
            with open(path, encoding='utf-8', errors='replace') as fh:
                names.update(re.findall(pattern, fh.read(), re.M))
    return names


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


def blank_literal(m):
    s = m.group(0)
    body = re.sub(r'[^\n]', ' ', s)
    if s[0] == '/':
        return body
    q = "'" if s[0] == "'" else '"'
    return q + body[1:-1] + q


def nows(s):
    return re.sub(r'\s+', '', s)


def matching(s, i):
    opener = s[i]
    closer = {'(': ')', '{': '}', '[': ']'}[opener]
    depth = 0
    for j in range(i, len(s)):
        if s[j] == opener:
            depth += 1
        elif s[j] == closer:
            depth -= 1
            if depth == 0:
                return j
    return -1


class View:
    def __init__(self, lines, directive, top):
        self.lines = lines
        self.directive = directive
        self.top = top
        self.text = '\n'.join(lines)
        self.starts = []
        pos = 0
        for line in lines:
            self.starts.append(pos)
            pos += len(line) + 1
        self.close_of = {}
        self.snaps = {}

    def line_of(self, off):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def crosses_directive(self, a, b):
        if self.directive is None:
            return False
        return any(self.directive[k] is not None for k in range(self.line_of(a), self.line_of(b) + 1))


class Source:
    def __init__(self, path):
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
        raw = text.split('\n')
        self.lines = LEX_RE.sub(blank_literal, text).split('\n')
        n = len(self.lines)
        self.kind = [None] * n
        self.head = [None] * n
        i = 0
        while i < n:
            m = DIRECTIVE_RE.match(self.lines[i])
            if not m:
                i += 1
                continue
            w = m.group(1)
            if w in ('if', 'ifdef', 'ifndef'):
                k = 'if'
            elif w in ('else', 'elif', 'elifdef', 'elifndef'):
                k = 'else'
            elif w == 'endif':
                k = 'endif'
            elif w == 'define':
                k = 'define'
            else:
                k = 'other'
            self.kind[i] = k
            self.head[i] = i
            j = i
            while j + 1 < n and raw[j].rstrip().endswith('\\'):
                j += 1
                self.kind[j] = 'cont'
                self.head[j] = i
            i = j + 1
        self.views = {}

    def view_key(self, idx):
        h = self.head[idx]
        return h if h is not None and self.kind[h] == 'define' else None

    def view(self, key):
        if key in self.views:
            return self.views[key]
        n = len(self.lines)
        if key is None:
            lines = ['' if self.kind[i] is not None else self.lines[i] for i in range(n)]
            v = View(lines, self.kind, 'ns')
        else:
            lines = [''] * n
            for j in range(key, n):
                if self.head[j] != key:
                    break
                t = self.lines[j]
                if j == key:
                    t = DEFINE_HEAD_RE.sub(lambda m: ' ' * len(m.group(0)), t)
                lines[j] = re.sub(r'\\(\s*)$', lambda m: ' ' + m.group(1), t)
            v = View(lines, None, 'func')
        self.views[key] = v
        return v


def strip_template(h):
    while h.startswith('template'):
        i = h.find('<')
        if i < 0:
            return h
        depth = 0
        for j in range(i, len(h)):
            if h[j] == '<':
                depth += 1
            elif h[j] == '>':
                depth -= 1
                if depth == 0:
                    break
        else:
            return h
        h = h[j + 1:].lstrip()
    return h


def strip_macro_prefix(h):
    while True:
        m = MACRO_HEAD_RE.match(h)
        if not m:
            return h
        close = matching(h, m.end() - 1)
        if close < 0 or not h[close + 1:].strip():
            return h
        h = h[close + 1:].lstrip()


def block_kind(parent, header):
    if parent in ('func', 'block'):
        return 'block'
    if parent == 'init':
        return 'init'
    h = strip_macro_prefix(ACCESS_RE.sub('', header).strip())
    if re.match(r'(?:inline\s+)?namespace\b', h) or re.match(r'extern\s*""', h):
        return 'ns'
    h2 = strip_template(h)
    if re.match(r'(?:typedef\s+)?(?:class|struct|union)\b', h2) and '(' not in h2:
        return 'class'
    if '(' in h and not h.endswith('='):
        return 'func'
    return 'init'


def scan(view, wants):
    stack = []
    saved = []
    depth = 0
    parts = []
    wants = sorted(set(wants))
    wi = 0
    for i, line in enumerate(view.lines):
        if view.directive is not None and view.directive[i] is not None:
            k = view.directive[i]
            if k == 'if':
                saved.append((list(stack), depth, list(parts)))
            elif k == 'else' and saved:
                stack, depth, parts = list(saved[-1][0]), saved[-1][1], list(saved[-1][2])
            elif k == 'endif' and saved:
                saved.pop()
            continue
        base = view.starts[i]
        pos = 0
        events = list(DELIM_RE.finditer(line))
        for e in range(len(events) + 1):
            limit = base + (events[e].start() if e < len(events) else len(line) + 1)
            while wi < len(wants) and wants[wi] < limit:
                col = max(wants[wi] - base, pos)
                view.snaps[wants[wi]] = (tuple(stack), ''.join(parts) + line[pos:col])
                wi += 1
            if e == len(events):
                break
            m = events[e]
            ch = m.group()
            parts.append(line[pos:m.start()])
            pos = m.end()
            if ch == '(':
                depth += 1
                parts.append('(')
            elif ch == ')':
                depth = max(depth - 1, 0)
                parts.append(')')
            elif ch == ';':
                if depth > 0:
                    parts.append(';')
                else:
                    parts = []
            elif ch == '{':
                parent = stack[-1][1] if stack else view.top
                header = ' '.join(''.join(parts).split())
                stack.append((base + m.start(), block_kind(parent, header), header, depth))
                depth = 0
                parts = []
            else:
                if stack:
                    top = stack.pop()
                    view.close_of[top[0]] = base + m.start()
                    depth = top[3]
                parts = []
        parts.append(line[pos:] + '\n')


def parse_construct(construct):
    out = []
    for p in TAG_RE.sub('', construct).split('; '):
        m = CASE_PART_RE.match(p)
        if m:
            out.append(('case', m.group('h'), None, m.group('code')))
            continue
        m = PART_RE.match(p)
        if not m:
            out.append(('?', p, None, None))
            continue
        h, op, code = m.group('h'), m.group('op'), m.group('code')
        for kind in ('other variable', 'call result', 'errsim expression'):
            if h.startswith(kind + ' '):
                out.append((kind, h[len(kind) + 1:], op, code))
                break
        else:
            if h.endswith(' (assigned in the comparison)'):
                out.append(('assign', h[:-len(' (assigned in the comparison)')], op, code))
            else:
                out.append(('var', h, op, code))
    return out


def statement_span(text, p):
    bal = 0
    i = p - 1
    while i >= 0:
        c = text[i]
        if c == ')':
            bal += 1
        elif c == '(':
            bal -= 1
        elif c in ';{}' and bal <= 0:
            break
        i -= 1
    start = i + 1
    bal = 0
    j = p
    while j < len(text):
        c = text[j]
        if c == '(':
            bal += 1
        elif c == ')':
            bal -= 1
        elif c == ';' and bal <= 0:
            return start, j, True
        elif c in '{}' and bal <= 0:
            return start, j, False
        j += 1
    return start, j, False


def tokens(expr):
    out = []
    pos = 0
    expr = expr.rstrip()
    while pos < len(expr):
        m = TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            return None
        pos = m.end()
        if m.group('logic'):
            out.append(('logic', m.group('logic')))
        elif m.group('cmp'):
            out.append(('cmp', m.group('cmp')))
        elif m.group('likely') or m.group('lp'):
            out.append(('lp', '('))
        elif m.group('rp'):
            out.append(('rp', ')'))
        elif m.group('id'):
            if m.group('call'):
                return None
            out.append(('id', nows(m.group('id'))))
        else:
            return None
    return out


def comparisons_only(expr, catalog):
    toks = tokens(expr)
    if not toks:
        return None
    comps = []

    def term(i):
        if i < len(toks) and toks[i][0] == 'lp':
            i = seq(i + 1)
            if i is None or i >= len(toks) or toks[i][0] != 'rp':
                return None
            return i + 1
        if i + 2 < len(toks) and toks[i][0] == 'id' and toks[i + 1][0] == 'cmp' and toks[i + 2][0] == 'id':
            comps.append((toks[i][1], toks[i + 2][1]))
            return i + 3
        return None

    def seq(i):
        i = term(i)
        while i is not None and i < len(toks) and toks[i][0] == 'logic':
            i = term(i + 1)
        return i

    if seq(0) != len(toks) or not comps:
        return None
    holders = set()
    for a, b in comps:
        ca = CODE_OPERAND_RE.fullmatch(a)
        cb = CODE_OPERAND_RE.fullmatch(b)
        ca = ca is not None and ca.group(1) in catalog
        cb = cb is not None and cb.group(1) in catalog
        if ca == cb:
            return None
        holders.add(b if ca else a)
    return holders.pop() if len(holders) == 1 else None


def return_shape(view, p, catalog):
    start, end, closed = statement_span(view.text, p)
    if not closed:
        return None
    stmt = LABEL_RE.sub('', view.text[start:end].strip())
    m = re.match(r'return\b', stmt)
    if not m:
        return None
    holder = comparisons_only(stmt[m.end():], catalog)
    if holder is None:
        return None
    return holder, start + view.text[start:end].find('return')


def bool_function_line(view, ret_off, symbol):
    if symbol == '-' or symbol.startswith('#define'):
        return None
    last = symbol.split('::')[-1]
    name_re = re.compile(r'(?<![\w~])' + re.escape(last) + r'\s*\(')
    first = view.line_of(ret_off)
    candidates = [(first, view.text[view.starts[first]:ret_off])]
    candidates += [(j, view.lines[j]) for j in range(first - 1, -1, -1)]
    for j, text in candidates:
        m = name_re.search(text)
        if m:
            prev = view.lines[j - 1] if j > 0 else ''
            if re.search(r'\bbool\b', text[:m.start()]) or re.search(r'\bbool\b', prev):
                return j
            return None
    return None


def parse_if_line(line):
    lead = len(line) - len(line.lstrip())
    s = line.strip()
    m = IF_LINE_RE.match(s)
    if not m:
        return None
    close = matching(s, m.end() - 1)
    if close < 0:
        return None
    after = s[close + 1:]
    if not after.lstrip().startswith('{'):
        return None
    brace = close + 1 + len(after) - len(after.lstrip())
    cond = s[m.end():close].strip()
    lm = LIKELY_RE.match(cond)
    if lm and matching(cond, lm.end() - 1) == len(cond) - 1:
        cond = cond[lm.end():-1].strip()
    return m.group(1) is not None, cond, lead + brace, not s[brace + 1:].strip()


def parse_comparison(cond):
    m = re.fullmatch(QUAL + r'(OB_[A-Z0-9_]+)\s*(==|!=)\s*(.+)', cond, re.S)
    if m:
        code, op, holder = m.group(1), m.group(2), m.group(3).strip()
    else:
        m = re.fullmatch(r'(.+?)\s*(==|!=)\s*' + QUAL + r'(OB_[A-Z0-9_]+)', cond, re.S)
        if not m:
            return None
        code, op, holder = m.group(3), m.group(2), m.group(1).strip()
    if re.search(r'[=!<>]=|&&|\|\||\?|,', holder) or holder.startswith('OB_'):
        return None
    return code, op, nows(holder)


def same_holder(line_holder, construct_holder):
    c = nows(construct_holder)
    if c.endswith('...'):
        return line_holder.startswith(c[:-3])
    return line_holder == c


def statements(body):
    out = []
    cur = []
    depth = 0
    for c in body:
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c in '{}' and depth <= 0:
            return None
        elif c == ';' and depth <= 0:
            s = ' '.join(''.join(cur).split())
            if s:
                out.append(s)
            cur = []
            continue
        cur.append(c)
    if ''.join(cur).strip():
        return None
    return out


def is_log(stmt):
    m = re.match(r'([A-Za-z_]\w*)\s*\(', stmt)
    if not m or m.group(1) in NOT_LOG_NAMES or not LOG_NAME_RE.fullmatch(m.group(1)):
        return False
    return matching(stmt, m.end() - 1) == len(stmt) - 1


def is_reset(stmt, holder):
    return re.fullmatch(re.escape(holder) + r'=' + nows(QUAL) + r'OB_SUCCESS', nows(stmt)) is not None


def rename_target(stmt):
    m = re.fullmatch(r'ret=(?:::)?(?:oceanbase::)?(?:common::)?(OB_[A-Z0-9_]+)', nows(stmt))
    return m.group(1) if m else None


def log_user_error_code(stmt):
    m = re.match(r'LOG_USER_ERROR\s*\(', stmt)
    if not m or matching(stmt, m.end() - 1) != len(stmt) - 1:
        return None
    first = stmt[m.end():-1].split(',')[0]
    c = CODE_OPERAND_RE.fullmatch(first.strip())
    return c.group(1) if c else None


def block_at(view, line_idx, col):
    open_off = view.starts[line_idx] + col
    close = view.close_of.get(open_off)
    if close is None or view.crosses_directive(open_off, close):
        return None
    return open_off, close


def next_token(view, off):
    m = re.compile(r'\S').search(view.text, off)
    if not m:
        return None, None
    if view.crosses_directive(off, m.start()):
        return None, None
    return m.start(), view.text[m.start():m.start() + 40]


def enclosing_loops(view, snap):
    stack, pending = snap
    if LOOP_WORD_RE.search(pending):
        return None
    loops = []
    for open_off, kind, header, _ in reversed(stack):
        if kind == 'func':
            break
        if kind != 'block':
            return None
        h = LABEL_RE.sub('', re.sub(r'^else\b\s*', '', header))
        if LOOP_WORD_RE.search(header) or MACRO_HEAD_RE.match(h):
            close = view.close_of.get(open_off)
            if close is None:
                return None
            text = header + ' ' + view.text[open_off + 1:close]
            if re.fullmatch(r'(?:.*\s)?do', header):
                end = view.text.find(';', close)
                text += ' ' + view.text[close + 1:end if end >= 0 else len(view.text)]
            loops.append(text)
    else:
        if view.top != 'func':
            return None
    return loops


def tests_code(text, code):
    return re.search(r'[=!]=\s*' + QUAL + r'\b' + code + r'\b|\b' + code + r'\s*[=!]=|\bcase\s+' + QUAL + code + r'\s*:',
                     text) is not None


def only(stmts, allowed):
    return all(any(test(s) for test in allowed) for s in stmts)


def returns_bool(view, p, symbol):
    snap = view.snaps.get(p)
    header = next((h for _, k, h, _ in reversed(snap[0]) if k == 'func'), None) if snap else None
    if header is None:
        return None
    h = strip_template(strip_macro_prefix(ACCESS_RE.sub('', header).strip()))
    m = re.search(r'(?<![\w~])' + re.escape(symbol.split('::')[-1]) + r'\s*\(', h)
    return re.search(r'\bbool\b', h[:m.start()] if m else h.split('(')[0]) is not None


def value_pattern(view, idx, p, parts, codes, shape, catalog):
    if OOM_CODE in codes and shape is None:
        return 'OUT_OF_MEMORY', 'names %s' % OOM_CODE
    init = [c for c in codes if c in INIT_CODES]
    if init and shape is None:
        return 'INIT_STATE', 'names %s' % init[0]
    parsed = parse_if_line(view.lines[idx])
    cmp = parse_comparison(parsed[1]) if parsed else None
    if not cmp or len(parts) != 1 or parts[0][3] != cmp[0]:
        return None
    kind, holder, op, code = parts[0]
    block = block_at(view, idx, parsed[2])
    if not block:
        return None
    if parsed[3] and kind == 'var' and holder == 'ret' and op == '==' and cmp[1:] == ('==', 'ret'):
        stmts = statements(view.text[block[0] + 1:block[1]])
        named = [t for t in (rename_target(s) for s in stmts or []) if t]
        if stmts and len(named) == 1:
            y = named[0]
            ok = y in catalog and y not in ('OB_SUCCESS', code)
            ok = ok and only(stmts, (lambda s: rename_target(s) == y, is_log, lambda s: log_user_error_code(s) == y))
            loops = enclosing_loops(view, view.snaps[p]) if ok and p in view.snaps else None
            if ok and loops is not None and not any(tests_code(t, y) for t in loops):
                return 'RENAMED', 'if (%s == ret) { ret = %s; } ends at line %d' % (code, y, view.line_of(block[1]) + 1)
    if not (all(c in ITER_CODES for c in codes) and kind in ('var', 'other variable', 'call result')
            and same_holder(cmp[2], holder) and op == cmp[1]):
        return None
    h = cmp[2]
    stmts = statements(view.text[block[0] + 1:block[1]])
    reset_or_log = (lambda s: is_reset(s, h), lambda s: s == 'break', is_log)
    if stmts is None:
        return None
    if op == '==' and only(stmts, reset_or_log):
        return 'END_OF_DATA', '(1) if (%s == %s) block, ends at line %d' % (code, h, view.line_of(block[1]) + 1)
    if op != '!=' or parsed[0] or not only(stmts, (is_log,)):
        return None
    at, tok = next_token(view, block[1] + 1)
    if at is None:
        return None
    if not re.match(r'else\b', tok):
        return 'END_OF_DATA', '(2) if (%s != %s) block, no else, ends at line %d' % (code, h, view.line_of(block[1]) + 1)
    at2, tok2 = next_token(view, at + 4)
    if at2 is None or not tok2.startswith('{'):
        return None
    close = view.close_of.get(at2)
    if close is None or view.crosses_directive(at2, close):
        return None
    else_stmts = statements(view.text[at2 + 1:close])
    if else_stmts is not None and only(else_stmts, reset_or_log):
        return 'END_OF_DATA', '(3) if (%s != %s) block, else block ends at line %d' % (code, h, view.line_of(close) + 1)
    return None


def propose(row, nt, src, catalog, want):
    f, line_no, symbol, construct = row[0], int(row[1]), row[2], row[3]
    for a, b, tsv_line, reason in nt.get(f, []):
        if a <= line_no <= b and reason != 'generated':
            return 'NOT_TRANSLATED', 'not-translated.tsv:%d (%s)' % (tsv_line, reason)
    if f == GRAMMAR:
        return 'NOT_TRANSLATED', 'the grammar %s (decisions.md Decision 13)' % GRAMMAR
    if f == DEFINE_H and DEFINE_H_SPAN[0] <= line_no <= DEFINE_H_SPAN[1]:
        return 'CODE_PREDICATE', '(a) %s:%d-%d (F3)' % (DEFINE_H, DEFINE_H_SPAN[0], DEFINE_H_SPAN[1])
    parts = parse_construct(construct)
    codes = [c[3] for c in parts if c[3]]
    if src is None:
        return 'READ', 'read: source line not found'
    idx = line_no - 1
    view = src.view(src.view_key(idx))
    p = want
    shape = None
    if p is not None and not any(c[0] == 'case' for c in parts):
        shape = return_shape(view, p, catalog)
    in_bool = returns_bool(view, p, symbol)
    if shape is not None:
        fl = bool_function_line(view, shape[1], symbol)
        if fl is not None and in_bool:
            return 'CODE_PREDICATE', '(b) return of comparisons of %s with codes; bool at line %d' % (shape[0], fl + 1)
    found = value_pattern(view, idx, p, parts, codes, shape, catalog)
    if found and in_bool:
        return 'READ', 'read: the %s pattern matched in a function that returns bool (value 2 may apply)' % found[0]
    if found:
        return found
    if codes and all(c in ITER_CODES for c in codes):
        return 'READ', 'read: OB_ITER_END or OB_ITER_STOP outside the three blocks'
    if shape is not None:
        return 'READ', 'read: a return of code comparisons, bool not found before the function name'
    if any(c in LOOKUP_CODES or LOOKUP_SUFFIX_RE.search(c) for c in codes):
        return 'READ', 'read: a lookup code (value 9 reads the producer)'
    if any(c in BUFFER_CODES for c in codes):
        return 'READ', 'read: a buffer code (value 10 reads the producer)'
    return 'READ', 'read: the branch and where the code goes decide'


def code_offset(view, idx, parts):
    line = view.lines[idx]
    best = None
    for kind, holder, op, code in parts:
        if not code:
            continue
        m = re.search(r'(?<![\w])' + code + r'\b', line)
        if m and (best is None or m.start() < best):
            best = m.start()
    return view.starts[idx] + best if best is not None else None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    header, rows = read_tsv(SWEEP)
    nt = not_translated_spans()
    catalog = load_catalog()
    by_file = collections.defaultdict(list)
    for n, (tsv_line, r) in enumerate(rows):
        by_file[r[0]].append(n)
    result = [None] * len(rows)
    for f, members in by_file.items():
        path = os.path.join(ROOT, f)
        src = Source(path) if os.path.exists(path) else None
        wants = {}
        if src is not None:
            per_view = collections.defaultdict(list)
            for n in members:
                idx = int(rows[n][1][1]) - 1
                if idx >= len(src.lines):
                    continue
                key = src.view_key(idx)
                view = src.view(key)
                off = code_offset(view, idx, parse_construct(rows[n][1][3]))
                wants[n] = off
                if off is not None:
                    per_view[key].append(off)
            for key, offs in per_view.items():
                scan(src.view(key), offs)
        for n in members:
            r = rows[n][1]
            ok_src = src if src is not None and int(r[1]) - 1 < len(src.lines) else None
            result[n] = r + list(propose(r, nt, ok_src, catalog, wants.get(n)))
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
