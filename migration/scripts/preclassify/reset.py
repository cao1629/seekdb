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
SWEEP = os.path.join(ROOT, 'migration', 'inventory', 'sweep', 'reset.tsv')
NOT_TRANSLATED = os.path.join(ROOT, 'migration', 'not-translated.tsv')
VOCABULARY = os.path.join(ROOT, 'migration', 'inventory', 'vocabulary', 'reset.md')
OUT = os.path.join(ROOT, 'migration', 'inventory', 'pre', 'reset.tsv')

ABOVE = 8
BELOW = 6
CHAIN = 32
DEEP = 400
TAB = 2

PFX = r'(?:(?:::)?(?:oceanbase::)?(?:common::)?)'
CODE = r'(?<![\w:])' + PFX + r'(?P<c>OB_[A-Z0-9_]+)\b'
NAME = r'(?<![\w])(?:ret|\w*_ret)\b'
EQ_RES = [re.compile(CODE + r'\s*==\s*\(*\s*' + NAME), re.compile(NAME + r'\s*\)*\s*==\s*' + CODE)]
NE_RES = [re.compile(CODE + r'\s*!=\s*\(*\s*' + NAME), re.compile(NAME + r'\s*\)*\s*!=\s*' + CODE)]
CASE_CODE_RE = re.compile(r'^case\s+' + PFX + r'(OB_[A-Z0-9_]+)\s*:(?!:)')

DECL_SUCC_RE = re.compile(r'^\s*int\s+ret\s*=\s*' + PFX + r'OB_SUCCESS\s*;\s*$')
INIT_SUCC_RE = re.compile(r'^\s*INIT_SUCC\s*\(\s*ret\s*\)\s*;\s*$')
STORE_RES = [
    re.compile(r'(?<![\w.>])ret\s*=(?!=)'),
    re.compile(r'\b(?:OB_FAIL|OB_SUCC|FAILEDx|CLICK_FAIL|CLICK_SUCC)\s*\((?!\s*ret\s*\))'),
    re.compile(r'\b(?:OZ|OX|CK|OV|OZX1|OZX2|COVER_SUCC)\s*\('),
]
COPY_RE = re.compile(r'(?<![\w.>])(?!ret\b)(?P<n>\**[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*|\[[^\]]*\])*)\s*=\s*ret\s*;')
MENTION_RE = re.compile(r'(?<![\w.>])ret\b')
WARN_CALL_RE = re.compile(r'\b(?P<w>LOG_USER_WARN|LOG_USER_NOTE|LOG_MYSQL_USER_WARN|LOG_MYSQL_USER_NOTE'
                          r'|FORWARD_USER_WARN|FORWARD_USER_NOTE)\s*\(')
WARN_COPY_RE = re.compile(r'\b(?P<w>warn\w*)\s*=\s*(?:ret|(?:common::)?OB_[A-Z0-9_]+)\s*;')
OWN_STORE_RE = re.compile(r'(?<![\w.>])ret\s*=\s*\(?\s*' + PFX + r'(?P<c>OB_[A-Z0-9_]+)\s*\)?\s*;')
GETTER_RE = re.compile(r'\b(?:result|res|row)\w*\s*\)?\s*(?:\.|->)\s*get_\w+\s*\(|\bEXTRACT_\w+\s*\(|\bGET_COL\w*\s*\(')
ERRSIM_RE = re.compile(r'^\s*ret\s*=\s*(?:OB_E\s*\(|EVENT_CALL\s*\(|\w+\s*\?\s*:)')
DECLARATION_RE = re.compile(r'^\s*(?:const\s+)?(?P<t>[A-Za-z_][\w:]*)\s+ret\s*=\s*(?:common::)?OB_SUCCESS\s*;')
PREDICATE_RE = re.compile(r'(?<![\w.>])(?!(?:OB_FAIL|OB_SUCC|OB_LIKELY|OB_UNLIKELY|K)\b)(?P<p>[A-Za-z_]\w*)\s*\(\s*'
                          r'(?:ret|\w+_ret)\s*[,)]')
IF_RE = re.compile(r'^(?:\}\s*)?(?P<e>else\s+)?if\s*\(')
ELSE_RE = re.compile(r'^(?:\}\s*)?else\s*(?:\{\s*)?$')
WHILE_RE = re.compile(r'^(?:\}\s*)?while\s*\(')
CASE_RE = re.compile(r'^case\b')
RESET_ELSE_RE = re.compile(r'^\s*(?:\}\s*)?else\b')
TOKEN_RE = re.compile(r'//[^\n]*|/\*.*?\*/|R"(?P<d>[^()\\\s"]{0,16})\(.*?\)(?P=d)"'
                      r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)

END_CODES = ('OB_ITER_END', 'OB_ITER_STOP')
LOOKUP_PARTS = ('NOT_EXIST', 'EXIST', 'NOT_FOUND', 'DUP', 'UNKNOWN', 'NO_SUCH')
LOOKUP_CODES = ('OB_EMPTY_RESULT', 'OB_READ_NOTHING', 'OB_EMPTY_RANGE', 'OB_ERR_NULL_VALUE', 'OB_ERR_BAD_FIELD_ERROR',
                'OB_ERR_BAD_DATABASE', 'OB_ITEM_NOT_SETTED', 'OB_BEYOND_THE_RANGE', 'OB_ERR_OUT_OF_LOWER_BOUND',
                'OB_ERR_OUT_OF_UPPER_BOUND', 'OB_CONFLICT_VALUE')
BUFFER_CODES = ('OB_SIZE_OVERFLOW', 'OB_BUF_NOT_ENOUGH', 'OB_HASH_FULL')
VALUES = ('NOT_TRANSLATED', 'DECLARATION', 'ERRSIM_POINT', 'CHOSEN_BY_FLAG', 'BECOMES_WARNING', 'END_OF_DATA',
          'LOOKUP_OUTCOME', 'BUFFER_TOO_SMALL', 'ALREADY_SUCCESS')


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
                spans[m.group(1)].append((int(m.group(2)), int(m.group(3)), tsv_line, row[1], row[0]))
            elif entry:
                spans[entry].append((0, 10 ** 9, tsv_line, row[1], row[0]))
    return spans


def strip_comments(text):
    def repl(m):
        s = m.group(0)
        newlines = '\n' * s.count('\n')
        if s.startswith('//'):
            return ''
        if s.startswith('/*'):
            return newlines or ' '
        if s.startswith('R'):
            return 'R""' + newlines
        return s[0] * 2 + newlines
    return TOKEN_RE.sub(repl, text)


class Source:
    def __init__(self):
        self.files = {}

    def lines(self, path):
        if path not in self.files:
            with open(os.path.join(ROOT, path), encoding='utf-8', errors='replace') as fh:
                raw = fh.read()
            text = strip_comments(raw)
            if text.count('\n') != raw.count('\n'):
                raise SystemExit('%s: comment removal changed the line count' % path)
            self.files[path] = [''] + [re.sub(r'\s*\\\s*$', '', x).rstrip().expandtabs(TAB)
                                       for x in text.split('\n')]
        return self.files[path]


def indent(s):
    return len(s) - len(s.lstrip())


def skip(s):
    t = s.strip()
    return not t or (t.startswith('#') and not t.startswith('#define'))


def balance(s):
    return s.count('(') - s.count(')')


def group(s, i):
    depth = 0
    for k in range(i, len(s)):
        if s[k] == '(':
            depth += 1
        elif s[k] == ')':
            depth -= 1
            if depth == 0:
                return s[i + 1:k], k
    return s[i + 1:], len(s)


def full_statement(L, a, j, stop):
    while a > 1 and a > j - CHAIN and balance(' '.join(x.strip() for x in L[a:j + 1])) < 0:
        a -= 1
    b = j
    text = ' '.join(x.strip() for x in L[a:b + 1])
    while balance(text) > 0 and b + 1 < stop:
        b += 1
        text += ' ' + L[b].strip()
    return a, b, text


def condition(text):
    m = re.search(r'\b(?:if|while)\s*\(', text)
    if not m:
        return ''
    return group(text, m.end() - 1)[0].strip()


class Opener:
    def __init__(self, L, a, b, text):
        self.a, self.b, self.text = a, b, text.strip()
        self.indent = indent(L[a])
        m = IF_RE.match(self.text)
        if m:
            self.kind = 'elseif' if m.group('e') else 'if'
        elif ELSE_RE.match(self.text):
            self.kind = 'else'
        elif WHILE_RE.match(self.text):
            self.kind = 'while'
        elif CASE_RE.match(self.text):
            self.kind = 'case'
        else:
            self.kind = 'other'
        self.cond = condition(self.text) if self.kind in ('if', 'elseif', 'while') else ''
        self.chain = None
        self.chain_complete = False


def find_opener(L, start, ind, lo, stop):
    j = start - 1
    while j >= lo:
        s = L[j]
        if skip(s) or indent(s) >= ind:
            j -= 1
            continue
        if s.strip() == '{':
            k = j - 1
            while k > 0 and k > j - CHAIN and skip(L[k]):
                k -= 1
            if k <= 0 or skip(L[k]):
                return None
            j = k
        a, b, text = full_statement(L, j, j, stop)
        return Opener(L, a, b, text)
    return None


def read_chain(L, line, ind, floor):
    conds = []
    j = line - 1
    while j >= floor:
        s = L[j]
        t = s.strip()
        if skip(s) or indent(s) > ind or t in ('{', '}'):
            j -= 1
            continue
        if indent(s) < ind:
            break
        m = IF_RE.match(t)
        if not m:
            break
        a, b, text = full_statement(L, j, j, line)
        conds.append((j, condition(text)))
        if not m.group('e'):
            return conds, True
        j = a - 1
    return conds, False


def codes_in(text, res):
    out = []
    for r in res:
        for m in r.finditer(text):
            c = m.group('c')
            if c != 'OB_SUCCESS' and c not in out:
                out.append(c)
    return out


def stores(s):
    if DECL_SUCC_RE.match(s) or INIT_SUCC_RE.match(s):
        return False
    return any(r.search(s) for r in STORE_RES)


def mentions(cond):
    return bool(MENTION_RE.search(cond)) or stores(cond)


def unwrap(cond):
    c = cond.strip()
    while True:
        m = re.match(r'^(?:OB_LIKELY|OB_UNLIKELY)?\s*\(', c)
        if not m:
            return c
        inner, k = group(c, m.end() - 1)
        if k != len(c) - 1:
            return c
        c = inner.strip()


def is_succ_test(cond):
    c = re.sub(r'\s+', '', unwrap(cond))
    return bool(re.match(r'^(?:OB_SUCC\(ret\)|' + PFX + r'OB_SUCCESS==ret|ret==' + PFX + r'OB_SUCCESS)$', c))


def is_stop_test(cond):
    c = unwrap(cond)
    m = re.match(r'^(?:OB_FAIL|CLICK_FAIL)\s*\(', c)
    if m:
        inner, k = group(c, m.end() - 1)
        return k == len(c) - 1 and inner.strip() != 'ret'
    m = re.match(r'^' + PFX + r'OB_SUCCESS\s*!=\s*(?=\(\s*ret\s*=(?!=))', c)
    if m:
        inner, k = group(c, m.end())
        return k == len(c) - 1
    return False


def opener_codes(o):
    out = []
    if o.kind in ('if', 'elseif'):
        out = [(c, o.a) for c in codes_in(o.cond, EQ_RES)]
    elif o.kind == 'case':
        m = CASE_CODE_RE.match(o.text)
        if m and m.group(1) != 'OB_SUCCESS':
            out = [(m.group(1), o.a)]
    if (o.kind == 'else' or (o.kind == 'elseif' and not out)) and o.chain:
        for j, cond in o.chain:
            out += [(c, j) for c in codes_in(cond, NE_RES)]
    return out


def is_fail_test(cond):
    c = re.sub(r'\s+', '', unwrap(cond))
    if re.match(r'^(?:OB_FAIL\(ret\)|' + PFX + r'OB_SUCCESS!=ret|ret!=' + PFX + r'OB_SUCCESS)$', c):
        return True
    return is_stop_test(cond)


def is_fresh_test(cond):
    if is_succ_test(cond):
        return True
    c = unwrap(cond)
    m = re.match(r'^(?:OB_SUCC|CLICK_SUCC)\s*\(', c)
    if m:
        inner, k = group(c, m.end() - 1)
        return k == len(c) - 1
    m = re.match(r'^' + PFX + r'OB_SUCCESS\s*==\s*(?=\(\s*ret\s*=(?!=))', c)
    if m:
        inner, k = group(c, m.end())
        return k == len(c) - 1
    return bool(re.match(r'^OB_SUCC\s*\(\s*ret\s*\)\s*&&', c))


def is_function_top(o):
    if o.kind != 'other':
        return False
    if o.indent == 0 or o.text.startswith('#define'):
        return True
    if '](' in o.text or re.match(r'^(?:for|while|switch|do|try|catch|return)\b', o.text):
        return False
    m = re.match(r'^[\w:<>,*&~\s]*?([A-Za-z_]\w*)\s*\(', o.text)
    if not m or not re.search(r'[a-z]', m.group(1)) or '=' in o.text[:m.start(1)]:
        return False
    return bool(re.search(r'\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?(?:noexcept\s*)?\{?\s*$', o.text))


def enclosing(L, line, ind):
    floor = max(1, line - DEEP)
    out = []
    start, cur, stop = line, ind, line
    while True:
        o = find_opener(L, start, cur, floor, stop)
        if o is None:
            break
        if o.kind in ('else', 'elseif'):
            o.chain, o.chain_complete = read_chain(L, o.a, o.indent, max(1, o.a - DEEP))
        out.append(o)
        if is_function_top(o):
            break
        start, cur, stop = o.a, o.indent, o.a
    return out


def tracepoint_before(L, line):
    for j in range(line - 1, max(1, line - ABOVE) - 1, -1):
        if ERRSIM_RE.match(L[j]):
            if not any(stores(L[k]) for k in range(j + 1, line)):
                return j
            return None
    return None


def outer_problem(site, flag_ternary):
    L = site.L
    outer = enclosing(L, site.line, site.ind)
    for o in outer:
        codes = opener_codes(o)
        if codes:
            return 'a test further out names %s at line %d' % codes[0]
        tests = ([(o.a, o.cond)] if o.cond else []) + list(o.chain or [])
        for j, cond in tests:
            if MENTION_RE.search(cond) and not stores(cond):
                t = tracepoint_before(L, j)
                if t:
                    return 'the test at line %d follows the tracepoint read at line %d' % (j, t)
    if not flag_ternary:
        return None
    inner_start = site.line
    for o in outer:
        for k in range(o.b + 1, inner_start):
            if not skip(L[k]) and stores(L[k]):
                return 'ret is stored at line %d before the ternary' % k
        if o.kind in ('if', 'elseif', 'while') and o.cond:
            if is_fresh_test(o.cond):
                return None
            if is_fail_test(o.cond):
                return 'the test at line %d lets a code in ret through' % o.a
            if stores(o.cond):
                return 'the test at line %d stores into ret' % o.a
        if o.kind == 'other' and re.match(r'^for\b', o.text) and re.search(r';\s*OB_SUCC\s*\(\s*ret\s*\)', o.text):
            return None
        if (o.kind == 'other' and re.match(r'^(?:for|do)\b', o.text)) or o.kind == 'while':
            return 'the loop at line %d can carry a code into the ternary' % o.a
        if o.kind in ('else', 'elseif'):
            if not o.chain_complete:
                return 'the chain of line %d is longer than the script reads' % o.a
            if any(is_stop_test(c) for _, c in o.chain):
                return None
            for j, c in o.chain:
                if is_fresh_test(c) or is_fail_test(c) or stores(c):
                    return 'the chain test at line %d lets a code in ret through' % j
            inner_start = o.chain[-1][0]
        else:
            inner_start = o.a
        if is_function_top(o):
            return None
    return 'the ternary is deeper than the script reads'


def function_rest(site):
    L = site.L
    outer = enclosing(L, site.line, site.ind)
    top = outer[-1] if outer and is_function_top(outer[-1]) else None
    if top is None:
        return None
    for k in range(site.line + 1, min(len(L), site.line + DEEP)):
        if not skip(L[k]) and indent(L[k]) <= top.indent and L[k].strip().startswith('}'):
            return range(site.line + 1, k)
    return None


def own_store(site, codes, upto):
    L = site.L
    outer = enclosing(L, site.line, site.ind)
    top = outer[-1].a if outer and is_function_top(outer[-1]) else max(1, site.line - DEEP)
    for k in range(top, upto):
        m = OWN_STORE_RE.search(L[k])
        if m and m.group('c') in codes:
            return m.group('c'), k
    return None


def code_line_above(L, line, lo):
    j = line - 1
    while j >= lo and skip(L[j]):
        j -= 1
    return j if j >= lo else None


def macro_params(L, line):
    j = line
    while j > 0 and not L[j].lstrip().startswith('#define'):
        j -= 1
    m = re.match(r'\s*#define\s+\w+\s*\(([^)]*)\)', L[j]) if j > 0 else None
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(',') if re.match(r'^[A-Za-z_]\w*$', p.strip())]


def caller_code_test(conds, params):
    for cond in conds:
        for p in params:
            if re.search(r'\(?\s*\b%s\b\s*\)?\s*[!=]=\s*\(*\s*' % re.escape(p) + NAME, cond) or \
               re.search(NAME + r'\s*\)*\s*[!=]=\s*\(?\s*\b%s\b' % re.escape(p), cond):
                return p
    return None


def ternary_condition(stmt):
    m = re.match(r'^\s*ret\s*=\s*', stmt)
    if not m:
        return None
    depth = 0
    for k in range(m.end(), len(stmt)):
        ch = stmt[k]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == '?' and depth == 0:
            return stmt[m.end():k]
        elif ch == ';' and depth == 0:
            return None
    return None


def reset_statement(L, line, stop):
    text = L[line].strip()
    b = line
    while ';' not in text and b + 1 <= stop:
        b += 1
        text += ' ' + L[b].strip()
    return text


class Site:
    def __init__(self, src, row):
        self.file, self.line, self.symbol, self.construct, self.text = row[0], int(row[1]), row[2], row[3], row[4]
        L = src.lines(self.file)
        self.L = L
        n = len(L) - 1
        line = self.line
        self.lo = max(1, line - ABOVE)
        self.hi = min(n, line + BELOW)
        self.chain_lo = max(1, line - CHAIN)
        self.reset = L[line]
        self.ind = indent(self.reset)
        self.stmt = reset_statement(L, line, self.hi)
        self.one_line_if = bool(IF_RE.match(self.reset.strip()))
        self.ternary = 'ternary' in self.construct
        self.false_it = 'FALSE_IT(' in self.reset
        self.block_above = []
        j = line - 1
        while j >= self.lo:
            if not skip(L[j]):
                if indent(L[j]) < self.ind:
                    break
                self.block_above.append(j)
            j -= 1
        self.block_below = []
        j = line + 1
        while j <= self.hi:
            if not skip(L[j]):
                if indent(L[j]) < self.ind:
                    break
                self.block_below.append(j)
            j += 1
        self.openers = []
        o1 = find_opener(L, line, self.ind, self.lo, line)
        if o1:
            self.openers.append(o1)
            o2 = find_opener(L, o1.a, o1.indent, self.lo, o1.a)
            if o2:
                self.openers.append(o2)
        for o in self.openers:
            if o.kind in ('else', 'elseif'):
                o.chain, o.chain_complete = read_chain(L, o.a, o.indent, self.chain_lo)
        self.own_cond = None
        if self.one_line_if:
            self.own_cond = condition(self.reset.strip())
        elif self.ternary:
            self.own_cond = ternary_condition(self.stmt)
        self.own_chain = None
        if RESET_ELSE_RE.match(self.reset):
            self.own_chain, _ = read_chain(L, line, self.ind, self.chain_lo)
        self.named = []
        self.named_at = {}
        self.collect_named()
        self.hidden = None
        for cond in self.conditions():
            m = PREDICATE_RE.search(cond)
            if m:
                self.hidden = 'test through the predicate %s' % m.group('p')
                break
        if not self.hidden and self.symbol.startswith('#define'):
            chains = [c for o in self.openers if o.chain for _, c in o.chain]
            p = caller_code_test(self.conditions() + chains + [c for _, c in self.own_chain or []],
                                 macro_params(L, line))
            if p:
                self.hidden = 'code test on the macro parameter %s' % p

    def add(self, codes, at):
        for c in codes:
            if c not in self.named:
                self.named.append(c)
                self.named_at[c] = at

    def collect_named(self):
        own = codes_in(self.own_cond, EQ_RES) if self.own_cond else []
        self.add(own, self.line)
        if self.own_chain and not own:
            for j, cond in self.own_chain:
                self.add(codes_in(cond, NE_RES), j)
        for o in self.openers:
            for c, j in opener_codes(o):
                self.add([c], j)

    def conditions(self):
        out = [o.cond for o in self.openers if o.cond]
        if self.own_cond:
            out.append(self.own_cond)
        return out

    def visible(self, regex):
        texts = [self.L[j] for j in range(self.lo, self.line)] + [o.text for o in self.openers]
        texts += [c for o in self.openers for _, c in o.chain or []] + [c for _, c in self.own_chain or []]
        return any(regex.search(t) for t in texts)


def propose(site, nt):
    L = site.L
    for a, b, tsv_line, reason, unit in nt.get(site.file, []):
        if a <= site.line <= b:
            return 'NOT_TRANSLATED', 'rule 1: not-translated.tsv:%d, %s, unit %s' % (tsv_line, reason, unit)
    m = DECLARATION_RE.match(site.reset)
    if m and m.group('t') not in ('return', 'else', 'case'):
        rest = function_rest(site) if m.group('t') == 'bool' else ()
        if rest is None:
            return 'READ', 'rule 2 check: declares bool ret, end of the function not found'
        for k in rest:
            if stores(L[k]) and not re.match(r'^\s*ret\s*=\s*' + PFX + r'OB_SUCCESS\s*;', L[k]):
                return 'READ', 'rule 2 check: the bool ret receives a store at line %d (4.2, open question 2)' % k
        return 'DECLARATION', 'rule 2: declares %s ret' % m.group('t')
    if 'errsim injection point' in site.construct:
        return 'ERRSIM_POINT', 'rule 3: errsim injection point'
    for j in range(site.line - 1, site.lo - 1, -1):
        if ERRSIM_RE.match(L[j]):
            if not any(stores(L[k]) for k in range(j + 1, site.line)):
                return 'ERRSIM_POINT', 'rule 3: tracepoint read into ret at line %d' % j
            break
    note = ''
    if 'code chosen by a flag' in site.construct and not site.named and not site.hidden:
        problem = outer_problem(site, True)
        if not problem:
            return 'CHOSEN_BY_FLAG', 'rule 4: flag ternary, no named code'
        note = '; rule 4 check: %s' % problem
    for j in sorted(site.block_above + site.block_below):
        m = WARN_CALL_RE.search(L[j])
        if m:
            return 'BECOMES_WARNING', 'rule 5: %s at line %d' % (m.group('w'), j)
        m = WARN_COPY_RE.search(L[j])
        if m:
            back = re.compile(r'(?<![\w.>])ret\s*=\s*%s\s*;|\breturn\s+%s\s*;' % ((re.escape(m.group('w')),) * 2))
            rest = function_rest(site)
            if rest is None:
                return 'READ', 'rule 5 check: warning copy %s at line %d, end of the function not found' % (m.group('w'), j)
            for k in rest:
                if back.search(L[k]):
                    return 'READ', 'rule 5 check: the warning copy %s at line %d is put back at line %d (4.8)' % (
                        m.group('w'), j, k)
            return 'BECOMES_WARNING', 'rule 5: %s at line %d' % (m.group('w'), j)
    for j in range(site.line - 1, site.lo - 1, -1):
        m = COPY_RE.search(L[j])
        if m:
            return 'READ', 'rule 6: copy of ret into %s at line %d' % (m.group('n'), j)
    if not (site.one_line_if or site.ternary or site.false_it):
        for j in site.block_above:
            if stores(L[j]):
                return 'READ', 'rule 6: store into ret at line %d' % j
    if site.hidden:
        return 'READ', 'section 10: %s' % site.hidden
    if site.named:
        for c in site.named:
            if c in END_CODES:
                return 'END_OF_DATA', 'rule 7: %s named at line %d' % (c, site.named_at[c])
        lookup = [c for c in site.named if c in LOOKUP_CODES or any(p in c for p in LOOKUP_PARTS)]
        if lookup:
            c = lookup[0]
            if lookup == ['OB_ERR_NULL_VALUE'] and not site.visible(GETTER_RE):
                return 'READ', 'rule 7 check: OB_ERR_NULL_VALUE named at line %d, no result-column getter in sight (4.11 (b))' % (
                    site.named_at[c])
            own = own_store(site, lookup, site.named_at[c])
            if own:
                return 'READ', 'rule 7 check: %s named at line %d, and the function stores %s itself at line %d (4.6, 4.11 (b))' % (
                    c, site.named_at[c], own[0], own[1])
            return 'LOOKUP_OUTCOME', 'rule 7: %s named at line %d' % (c, site.named_at[c])
        buffer = [c for c in site.named if c in BUFFER_CODES]
        if buffer:
            c = buffer[0]
            own = own_store(site, buffer, site.named_at[c])
            if own:
                return 'READ', 'rule 7 check: %s named at line %d, and the function stores %s itself at line %d (4.6, 4.12)' % (
                    c, site.named_at[c], own[0], own[1])
            return 'BUFFER_TOO_SMALL', 'rule 7: %s named at line %d' % (c, site.named_at[c])
        return 'READ', 'rule 7: other named code %s at line %d' % (
            ', '.join(site.named), site.named_at[site.named[0]])
    pattern = already_success(site)
    if pattern:
        problem = outer_problem(site, False)
        if not problem:
            return 'ALREADY_SUCCESS', pattern
        return 'READ', '%s; rule 8 check: %s' % (pattern, problem)
    return 'READ', 'rule 9: no rule holds' + note


def already_success(site):
    L = site.L
    j = code_line_above(L, site.line, site.lo)
    if j is not None and (DECL_SUCC_RE.match(L[j]) or INIT_SUCC_RE.match(L[j])):
        return 'rule 8: declaration just above, line %d' % j
    o = site.openers[0] if site.openers else None
    if o and o.kind == 'if' and not mentions(o.cond) and \
            all(skip(L[k]) or L[k].strip() == '{' for k in range(o.b + 1, site.line)):
        j = code_line_above(L, o.a, site.lo)
        if j is not None and (DECL_SUCC_RE.match(L[j]) or INIT_SUCC_RE.match(L[j])):
            return 'rule 8: first statement under the if at line %d after the declaration at line %d' % (o.a, j)
    if o and o.kind in ('if', 'elseif', 'while') and is_succ_test(o.cond):
        return 'rule 8: opener condition %s at line %d' % (re.sub(r'\s+', ' ', o.cond), o.a)
    if o and (o.kind == 'else' or (o.kind == 'elseif' and not mentions(o.cond))) and o.chain_complete:
        stops = [j for j, c in o.chain if is_stop_test(c)]
        others = [j for j, c in o.chain if not is_stop_test(c) and mentions(c)]
        if stops and not others:
            return 'rule 8: %s after the stop test at line %d' % (o.kind.replace('elseif', 'else if'), stops[-1])
    return None


def section9():
    rows = []
    with open(VOCABULARY, encoding='utf-8') as fh:
        for line in fh:
            m = re.match(r'^\| `(src/[^`:]+):(\d+)` \| ([A-Z_]+) \|', line)
            if m:
                rows.append((m.group(1), int(m.group(2)), m.group(3)))
    return rows


def run():
    header, rows = read_tsv(SWEEP)
    nt = not_translated_spans()
    src = Source()
    result = []
    for tsv_line, r in rows:
        proposed, pattern = propose(Site(src, r), nt)
        result.append((tsv_line, r, proposed, pattern))
    return header, result


def check(result):
    index = {(r[0], int(r[1])): (proposed, pattern) for _, r, proposed, pattern in result}
    rows = section9()
    proposals = agree = 0
    for f, line, value in rows:
        proposed, pattern = index[(f, line)]
        if proposed == 'READ':
            continue
        proposals += 1
        if proposed == value:
            agree += 1
        else:
            print('%s:%d\trecorded %s\tproposed %s\t%s' % (f, line, value, proposed, pattern))
    print('section 9 rows\t%d' % len(rows))
    print('proposals\t%d' % proposals)
    print('equal to the recorded value\t%d' % agree)


def main():
    args = sys.argv[1:]
    header, result = run()
    if args and args[0] == 'check':
        check(result)
        return
    out = args[0] if args else OUT
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write('\t'.join(header + ['proposed', 'pattern']) + '\n')
        for _, r, proposed, pattern in result:
            fh.write('\t'.join(r + [proposed, pattern]) + '\n')
    counts = collections.Counter(proposed for _, _, proposed, _ in result)
    for value in VALUES + ('READ',):
        print('%s\t%d' % (value, counts.get(value, 0)))
    print('rows\t%d' % len(result))
    print('READ share\t%.1f%%' % (100.0 * counts.get('READ', 0) / len(result)))


if __name__ == '__main__':
    main()
