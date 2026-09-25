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

import argparse
import json
import os
import re
import shlex
import sys
import time
from collections import defaultdict
from multiprocessing import Pool

FORMAT_VERSION = 1

STRIP_RE = re.compile(r"""
    (?P<lc>//(?:[^\n\\]|\\\n|\\.)*)
  | (?P<bc>/\*.*?\*/)
  | (?P<raw>\bR"(?P<rd>[^()\\\s"]{0,16})\(.*?\)(?P=rd)")
  | (?P<str>(?:\b(?:u8|u|U|L))?"(?:[^"\\\n]|\\\n|\\.)*")
  | (?P<num>(?<![\w.])\.?\d(?:[\w.']|[eEpP][+-])*)
  | (?P<chr>(?:\b(?:u8|u|U|L))?'(?:[^'\\\n]|\\.)*')
""", re.X | re.S)

TOK_RE = re.compile(r"""
    (?P<ws>[ \t\r\f\v]+|\\\n)
  | (?P<raw>\bR"(?P<rd>[^()\\\s"]{0,16})\(.*?\)(?P=rd)")
  | (?P<str>(?:\b(?:u8|u|U|L))?"(?:[^"\\\n]|\\.)*")
  | (?P<num>\.?\d(?:[\w.']|[eEpP][+-])*)
  | (?P<chr>(?:\b(?:u8|u|U|L))?'(?:[^'\\\n]|\\.)*')
  | (?P<id>[A-Za-z_$][\w$]*)
  | (?P<op>::|->\*|->|\.\.\.|<<=|>>=|<=>|<<|>>|<=|>=|==|!=|&&|\|\||\+\+|--|[-+*/%&|^]=|\.\*|\#\#|\S)
""", re.X | re.S)

DIRECTIVE_RE = re.compile(r"^[ \t]*#[ \t]*(\w*)(.*)$", re.S)
DEFINE_RE = re.compile(r"^[ \t]*([A-Za-z_]\w*)(\([^)]*\))?(.*)$", re.S)
DEFINE_LINE_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(\([^)]*\))?((?:[^\n\\]|\\\n|\\.)*)", re.M)
INCLUDE_LINE_RE = re.compile(r"^[ \t]*#[ \t]*include[ \t]*([\"<])([^\">]+)[\">]", re.M)
MACRO_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
INT_RE = re.compile(r"^(-?)\s*(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)[uUlL]*$")
EDGE_LINE_RE = re.compile(r"^L(\d+)(?:\{(.*)\})?")
LINK_EV_RE = re.compile(r"^L(\d+)(?:\{[^}]*\})? (\S+) defined at L(\d+)")
STRING_BODY_RE = re.compile(r'^"(?:[^"\\]|\\.)*"$')

HEADER_LIKE = (".h", ".hpp", ".hxx", ".ipp", ".def", ".inc")
IMPL_EXTS = (".c", ".cc", ".cpp", ".cxx")

ATTR_WORDS = {"__attribute__", "__declspec", "alignas", "_Alignas", "__asm__", "asm", "__asm", "_Pragma",
              "__pragma", "__extension__"}
NOT_CALLEE = {"decltype", "sizeof", "alignof", "__alignof__", "noexcept", "throw", "static_assert", "typeof",
              "__typeof__", "__typeof", "requires", "if", "while", "for", "switch", "return", "case", "new",
              "delete", "catch", "defined", "operator"} | ATTR_WORDS
TAIL_WORDS = {"const", "volatile", "noexcept", "throw", "override", "final", "try", "requires", "mutable",
              "__restrict", "__restrict__", "restrict", "&", "&&", "default", "delete", "0", "="} | ATTR_WORDS
SPECIFIERS = {"static", "inline", "virtual", "explicit", "extern", "constexpr", "consteval", "constinit", "friend",
              "typedef", "mutable", "thread_local", "__thread", "register", "__inline", "__inline__", "OB_INLINE",
              "OB_NOINLINE", "__forceinline", "_Thread_local", "volatile", "const", "typename"}
BUILTIN_TYPES = {"void", "bool", "_Bool", "char", "wchar_t", "char8_t", "char16_t", "char32_t", "short", "int",
                 "long", "float", "double", "signed", "unsigned", "__int128", "auto", "__int128_t", "__uint128_t"}
CLASS_KEYS = {"class", "struct", "union"}
ACCESS_WORDS = {"public", "private", "protected"}
CONTINUATION = {";", "{", ":", "const", "->", "=", "override", "final", "noexcept", "throw", "&", "&&",
                "__attribute__", ",", ")", "volatile", "(", "[", "::", "<", "*", ">", "?", "."}
RUST_KEYWORDS = {"as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum", "extern",
                 "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub",
                 "ref", "return", "self", "Self", "static", "struct", "super", "trait", "true", "type", "unsafe",
                 "use", "where", "while", "abstract", "become", "box", "do", "final", "macro", "override", "priv",
                 "typeof", "unsized", "virtual", "yield", "try", "gen"}
RUST_UNDERSCORE = {"self", "Self", "super", "crate"}
MACRO_TEXT_CAP = 240
USE_TEXT_CAP = 240

GLOBAL_MACROS = {}
NAMES = {}


class Src:
    __slots__ = ("path", "clean")

    def __init__(self, path, clean):
        self.path = path
        self.clean = clean


class Tok:
    __slots__ = ("kind", "text", "line", "start", "end", "src", "ev")

    def __init__(self, kind, text, line, start, end, src, ev):
        self.kind = kind
        self.text = text
        self.line = line
        self.start = start
        self.end = end
        self.src = src
        self.ev = ev


SQUASH_RE = re.compile(r"""\bR"([^()\\\s"]{0,16})\(.*?\)\1"|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\s+""", re.S)


def squash(s):
    return SQUASH_RE.sub(lambda m: " " if m.group(0)[0].isspace() else m.group(0), s).strip()


def strip_comments(text):
    out = []
    pos = 0
    for m in STRIP_RE.finditer(text):
        if m.lastgroup in ("lc", "bc"):
            out.append(text[pos:m.start()])
            out.append("".join("\n" if c == "\n" else " " for c in m.group(0)))
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def lex(path, text):
    clean = strip_comments(text)
    src = Src(path, clean)
    lines = clean.split("\n")
    offsets = []
    off = 0
    for ln in lines:
        offsets.append(off)
        off += len(ln) + 1
    events = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.lstrip().startswith("#"):
            start = i
            parts = [ln]
            while parts[-1].endswith("\\") and i + 1 < n:
                parts[-1] = parts[-1][:-1]
                i += 1
                parts.append(lines[i])
            m = DIRECTIVE_RE.match(" ".join(parts))
            events.append(("dir", start + 1, m.group(1), m.group(2).strip(), i + 1))
            i += 1
            continue
        base = offsets[i]
        if ln.endswith("\\"):
            ln = ln[:-1] + " "
        for m in TOK_RE.finditer(ln):
            k = m.lastgroup
            if k == "ws":
                continue
            if k in ("raw", "str", "chr"):
                k = "lit"
            events.append(("tok", Tok(k, m.group(0), i + 1, base + m.start(), base + m.end(), src, len(events))))
        i += 1
    return src, events


def synth_tokens(text, like):
    out = []
    for m in TOK_RE.finditer(text):
        k = m.lastgroup
        if k == "ws":
            continue
        if k in ("raw", "str", "chr"):
            k = "lit"
        out.append(Tok(k, m.group(0), like.line, None, None, like.src, None))
    return out


def join_texts(texts):
    out = ""
    for x in texts:
        if not out:
            out = x
            continue
        a = out[-1]
        b = x[0]
        if b in ",;)]" or a in "([" or x == "::" or out.endswith("::") or \
                (b in "([" and (a.isalnum() or a == "_" or a in ")]")):
            out += x
        else:
            out += " " + x
    return out


def render(toks):
    out = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.kind in ("tblk", "iblk"):
            out.append("{...}")
            i += 1
            continue
        if t.start is None:
            j = i
            while j + 1 < n and toks[j + 1].start is None and toks[j + 1].kind not in ("tblk", "iblk"):
                j += 1
            out.append(join_texts([x.text for x in toks[i:j + 1]]))
            i = j + 1
            continue
        j = i
        while j + 1 < n and toks[j + 1].src is t.src and toks[j + 1].start is not None and \
                toks[j + 1].kind not in ("tblk", "iblk") and toks[j + 1].ev == toks[j].ev + 1:
            j += 1
        out.append(t.src.clean[t.start:toks[j].end].replace("\\\n", " "))
        i = j + 1
    return squash(" ".join(out))


def match_close(toks, i):
    open_t = toks[i].text
    close_t = {"(": ")", "[": "]", "{": "}", "<": ">"}[open_t]
    depth = 0
    j = i
    n = len(toks)
    while j < n:
        x = toks[j].text
        if open_t == "<":
            if x in ("(", "[", "{") and j != i:
                k = match_close(toks, j)
                if k is None:
                    return None
                j = k + 1
                continue
            if x == "<":
                depth += 1
            elif x == ">":
                depth -= 1
            elif x == ">>":
                depth -= 2
                if depth < 0:
                    return j
            elif x in (";", "{", "}"):
                return None
        else:
            if x == open_t:
                depth += 1
            elif x == close_t:
                depth -= 1
        if depth == 0:
            return j
        j += 1
    return None


def angle_opens(toks, i):
    if i == 0:
        return False
    p = toks[i - 1]
    if p.kind == "id" and p.text != "operator":
        return True
    return p.text == "template"


def skip_attrs(toks, i):
    n = len(toks)
    while i < n:
        x = toks[i].text
        if x in ATTR_WORDS and i + 1 < n and toks[i + 1].text == "(":
            j = match_close(toks, i + 1)
            if j is None:
                return n
            i = j + 1
        elif x == "[" and i + 1 < n and toks[i + 1].text == "[":
            j = match_close(toks, i)
            if j is None:
                return n
            i = j + 1
        else:
            break
    return i


def template_prefix(toks):
    i = 0
    n = len(toks)
    while i < n and toks[i].text == "template" and i + 1 < n and toks[i + 1].text == "<":
        j = match_close(toks, i + 1)
        if j is None:
            break
        i = j + 1
    return i


def top_split(toks, sep=","):
    parts = []
    cur = []
    depth = 0
    for t in toks:
        x = t.text
        if x in ("(", "[", "{"):
            depth += 1
        elif x in (")", "]", "}"):
            depth -= 1
        if depth == 0 and x == sep:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    parts.append(cur)
    return parts


def decl_split(toks):
    parts = []
    cur = []
    depth = 0
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        x = t.text
        if x == "<" and angle_opens(toks, i):
            j = match_close(toks, i)
            if j is not None:
                cur.extend(toks[i:j + 1])
                i = j + 1
                continue
        if x in ("(", "[", "{"):
            depth += 1
        elif x in (")", "]", "}"):
            depth -= 1
        if depth == 0 and x == ",":
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
        i += 1
    parts.append(cur)
    return parts


GUARD_RE = re.compile(r"^\s*#\s*(?:ifndef\s+(\w+)\s*\n\s*#\s*define\s+(\w+)|pragma\s+once)", re.M)


def has_guard(text):
    clean = strip_comments(text)
    m = GUARD_RE.search(clean)
    if m is None:
        return False
    if m.group(1) is not None and m.group(1) != m.group(2):
        return False
    head = clean[:m.start()]
    return not re.search(r"^\s*[^#\s]", head, re.M)


def is_macro_name(x):
    return len(x) > 1 and bool(MACRO_NAME_RE.match(x))


def is_prefix_macro(name, env=None):
    m = env.get(name) if env is not None and name in env else GLOBAL_MACROS.get(name)
    if m is None:
        return False
    toks = [x.group(0) for x in TOK_RE.finditer(m[1]) if x.lastgroup != "ws"]
    i = 0
    n = len(toks)
    while i < n:
        x = toks[i]
        if x in ATTR_WORDS and i + 1 < n and toks[i + 1] == "(":
            depth = 0
            while i < n:
                if toks[i] == "(":
                    depth += 1
                elif toks[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
            continue
        if x in SPECIFIERS or x in ("template", "typename") or is_macro_name(x):
            i += 1
            continue
        return False
    return True


def pp_eval(name, rest, env):
    if name == "ifdef":
        return rest.split()[0] in env if rest.split() else False
    if name == "ifndef":
        return rest.split()[0] not in env if rest.split() else True
    toks = [m.group(0) for m in TOK_RE.finditer(rest) if m.lastgroup != "ws"]
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def prim():
        t = peek()
        if t is None:
            return 0
        if t == "!":
            take()
            return 0 if prim() else 1
        if t == "(":
            take()
            v = orx()
            if peek() == ")":
                take()
            return v
        if t == "defined":
            take()
            if peek() == "(":
                take()
                nm = take()
                if peek() == ")":
                    take()
            else:
                nm = take()
            return 1 if nm in env else 0
        take()
        m = INT_RE.match(t)
        if m:
            return int(m.group(2), 0) if not m.group(2).startswith(("0b", "0B")) else int(m.group(2)[2:], 2)
        if t in env and env[t][0] is None:
            b = env[t][1].strip()
            m = INT_RE.match(b)
            if m:
                return int(m.group(2), 0)
            return 1 if b == "" else 0
        return 0

    def cmpx():
        v = prim()
        while peek() in ("==", "!=", "<", ">", "<=", ">="):
            op = take()
            w = prim()
            v = {"==": v == w, "!=": v != w, "<": v < w, ">": v > w, "<=": v <= w, ">=": v >= w}[op]
            v = 1 if v else 0
        return v

    def andx():
        v = cmpx()
        while peek() == "&&":
            take()
            w = cmpx()
            v = 1 if (v and w) else 0
        return v

    def orx():
        v = andx()
        while peek() == "||":
            take()
            w = andx()
            v = 1 if (v or w) else 0
        return v

    try:
        return bool(orx())
    except Exception:
        return False


def expand_macros(toks, env, local, depth=0, hide=frozenset()):
    out = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        m = env.get(t.text) if t.kind == "id" and t.text in local and t.text not in hide else None
        if m is not None and m[0] is not None and i + 1 < n and toks[i + 1].text == "(" and depth < 8:
            j = match_close(toks, i + 1)
            if j is None:
                out.append(t)
                i += 1
                continue
            args = top_split(toks[i + 2:j])
            params = m[0]
            amap = {}
            for k, p in enumerate(params):
                if p == "...":
                    amap["__VA_ARGS__"] = [x for a in args[k:] for x in a + [Tok("op", ",", t.line, None, None,
                                                                                    t.src, None)]][:-1]
                elif p.endswith("..."):
                    amap[p[:-3]] = [x for a in args[k:] for x in a + [Tok("op", ",", t.line, None, None,
                                                                           t.src, None)]][:-1]
                else:
                    amap[p] = args[k] if k < len(args) else []
            body = synth_tokens(m[1], t)
            res = []
            k = 0
            while k < len(body):
                b = body[k]
                if b.text == "#" and k + 1 < len(body) and body[k + 1].text in amap:
                    s = join_texts([x.text for x in amap[body[k + 1].text]])
                    res.append(Tok("lit", '"%s"' % s, t.line, None, None, t.src, None))
                    k += 2
                    continue
                if b.text == "##" and res and k + 1 < len(body):
                    nxt = body[k + 1]
                    rep = amap.get(nxt.text, [nxt])
                    if rep:
                        prev = res.pop()
                        res.append(Tok("id", prev.text + rep[0].text, t.line, None, None, t.src, None))
                        res.extend(rep[1:])
                    k += 2
                    continue
                if b.kind == "id" and b.text in amap:
                    res.extend(Tok(x.kind, x.text, t.line, None, None, t.src, None) for x in amap[b.text])
                else:
                    res.append(Tok(b.kind, b.text, t.line, None, None, t.src, None))
                k += 1
            out.extend(expand_macros(res, env, local, depth + 1, hide | {t.text}))
            i = j + 1
            continue
        out.append(t)
        i += 1
    return out


SCOPE_WRAPPER_RE = re.compile(r'(?:(?:extern "C(?:\+\+)?" \{|namespace(?: [\w:]+)* \{|\}|;) ?)+')
DECL_START = {"class", "struct", "union", "enum", "typedef", "using", "public", "private", "protected", "friend",
              "namespace"}


def declares(toks):
    depth = 0
    n = len(toks)
    i = 0
    while i < n:
        x = toks[i].text
        if x in ("{", "[") or (x == "(" and depth > 0):
            depth += 1
        elif x in ("}", "]") or (x == ")" and depth > 0):
            depth -= 1
        elif depth == 0:
            if x in DECL_START:
                return True
            if x == "(" and i > 0 and toks[i - 1].kind == "id" and toks[i - 1].text not in NOT_CALLEE:
                j = match_close(toks, i)
                if j is None:
                    return False
                k = j + 1
                while k < n and (toks[k].text in TAIL_WORDS or toks[k].kind in ("num", "id") and
                                 is_macro_name(toks[k].text)):
                    k += 1
                if k < n and toks[k].text in (";", "{", ":"):
                    return True
                i = j + 1
                continue
        i += 1
    return False


class Cond:
    __slots__ = ("chain", "branch", "text")

    def __init__(self, chain, branch, text):
        self.chain = chain
        self.branch = branch
        self.text = text


class Decl:
    __slots__ = ("kind", "name", "line", "text", "access", "conds", "parent", "members", "scope", "file",
                 "rust", "extra", "order")

    def __init__(self, kind, name, line, text, access, conds, parent, scope, file, order):
        self.kind = kind
        self.name = name
        self.line = line
        self.text = text
        self.access = access
        self.conds = conds
        self.parent = parent
        self.members = []
        self.scope = scope
        self.file = file
        self.rust = None
        self.extra = {}
        self.order = order


class Frame:
    __slots__ = ("kind", "name", "access", "decl", "head", "raw", "done", "count")

    def __init__(self, kind, name, access, decl, head):
        self.kind = kind
        self.name = name
        self.access = access
        self.decl = decl
        self.head = head
        self.raw = [] if kind == "enum" else None
        self.done = [] if kind == "enum" else None
        self.count = 1


class HeaderParser:
    def __init__(self, path, text, loader=None):
        self.path = path
        self.loader = loader
        self.src, self.events = lex(path, text)
        self.decls = []
        self.macros = []
        self.undefs = {}
        self.notes = []
        self.forward = 0
        self.skipped_defs = 0
        self.inactive = 0
        self.counter = 0
        self.guard = None
        self.guard_names = set()
        self.fragment = False
        self.gen_from = None
        self.cur_file = None
        self.pack = None
        self.pack_stack = []
        self.last_closed = None
        self.alts = None
        self.alts_base = 0
        self.pending_cut = None
        self.seq = None
        self.cut_decls = 0
        self.in_alt = False

    def cond_list(self):
        return [c for c in self.cond_stack if c is not None]

    def run(self):
        self.stack = [Frame("file", "", None, None, [])]
        self.pending = []
        self.pending_conds = None
        self.list_entries = defaultdict(int)
        self.skip = 0
        self.skip_kind = None
        self.skip_start = None
        self.cond_stack = []
        self.chains = []
        self.chain_id = 0
        self.env = {}
        self.local = set()
        self.guard = self.find_guard()
        self.process(self.events, top=True)
        if self.pending:
            self.flush(None)
        if self.seq is not None:
            self.notes.append("line %d: the declarations %s opens are not listed: its braces do not close" % (
                self.seq["line"], self.seq["name"]))
            self.seq = None
        if len(self.stack) > 1:
            self.notes.append("unbalanced braces: %d scope(s) still open at the end of the file (innermost: %s %s)"
                              % (len(self.stack) - 1, self.stack[-1].kind, self.stack[-1].name or "(anonymous)"))
        self.drop_redeclarations()
        return self

    def drop_redeclarations(self):
        seen = {}
        keep = []
        self.redeclared = 0
        for d in self.decls:
            if d.kind == "function" and not d.extra.get("spec"):
                key = (d.scope, d.name, d.extra.get("params"), d.extra.get("ret"),
                       tuple((c.chain, c.branch) for c in d.conds))
                if key in seen:
                    self.redeclared += 1
                    continue
                seen[key] = d
            keep.append(d)
        self.decls = keep

    def process(self, events, top):
        self.event_pos = {id(e): k for k, e in enumerate(events)}
        self.chain_end = {}
        self.last_dir = None
        opened = []
        for k, e in enumerate(events):
            if e[0] != "dir":
                continue
            self.last_dir = k
            if e[2] in ("if", "ifdef", "ifndef"):
                opened.append(k)
            elif e[2] == "endif" and opened:
                self.chain_end[opened.pop()] = k
        inactive_depth = 0
        for ev in events:
            if ev[0] == "dir":
                _, line, name, rest, endline = ev
                if inactive_depth:
                    if name in ("if", "ifdef", "ifndef"):
                        inactive_depth += 1
                    elif name == "endif":
                        inactive_depth -= 1
                        if inactive_depth == 0:
                            self.end_chain(active_last=False)
                            self.inactive += line - self.inactive_from
                    elif name in ("elif", "else") and inactive_depth == 1:
                        ch = self.chains[-1]
                        if ch["taken"]:
                            continue
                        if name == "elif" and self.const_value(rest) is False:
                            ch["branch"] += 1
                            continue
                        self.inactive += line - self.inactive_from
                        inactive_depth = 0
                        self.begin_branch(name, rest)
                    continue
                if name in ("if", "ifdef", "ifndef"):
                    is_guard = (top and self.guard is not None and line == self.guard[0]) or \
                        self.guard_like(events, ev, name, rest)
                    self.chain_id += 1
                    ch = {"id": self.chain_id, "branch": 0, "snap": self.snapshot(), "ends": [],
                          "first": self.cond_text(name, rest), "taken": False, "guard": is_guard, "bconds": []}
                    self.chains.append(ch)
                    v = None if is_guard or name != "if" else self.const_value(rest)
                    if v is False:
                        self.cond_stack.append(None)
                        inactive_depth = 1
                        self.inactive_from = line
                        continue
                    if v is True:
                        ch["taken"] = True
                    c0 = None if is_guard or v is True else Cond(ch["id"], 0, "#" + name + " " + squash(rest))
                    ch["bconds"].append(c0)
                    self.cond_stack.append(c0)
                elif name in ("elif", "else"):
                    if not self.chains:
                        continue
                    ch = self.chains[-1]
                    ch["ends"].append(self.snapshot())
                    self.restore(ch["snap"])
                    if ch["taken"] or (name == "elif" and self.const_value(rest) is False):
                        if self.cond_stack:
                            self.cond_stack.pop()
                        self.cond_stack.append(None)
                        ch["branch"] += 1
                        inactive_depth = 1
                        self.inactive_from = line
                        continue
                    if self.cond_stack:
                        self.cond_stack.pop()
                    self.begin_branch(name, rest, pop=False)
                elif name == "endif":
                    self.end_chain()
                elif name == "define":
                    self.on_define(line, rest, endline)
                elif name == "undef":
                    nm = rest.split()[0] if rest.split() else ""
                    self.flush_enum_raw()
                    self.undefs.setdefault(nm, []).append(line)
                    self.env.pop(nm, None)
                elif name == "include":
                    self.on_include(line, rest)
                elif name == "pragma":
                    self.on_pragma(rest)
                continue
            if inactive_depth:
                continue
            self.feed(ev[1])

    def guard_like(self, events, ev, name, rest):
        gname = None
        if name == "ifndef":
            gname = rest.split()[0] if rest.split() else None
        elif name == "if":
            m = re.match(r"^!\s*defined\s*\(?\s*(\w+)\s*\)?\s*$", rest)
            if m:
                gname = m.group(1)
        if not gname:
            return False
        k = self.event_pos.get(id(ev))
        if k is None:
            return False
        for e in events[k + 1:]:
            if e[0] == "dir":
                words = e[3].split()
                if e[2] == "define" and (words or [""])[0] == gname and (len(words) == 1 or words[1:] == ["1"]):
                    end = self.chain_end.get(k)
                    if end is None:
                        return False
                    span = events[end][1] - ev[1]
                    if span >= 20 or end == self.last_dir:
                        self.guard_names.add(gname)
                        return True
                return False
            return False
        return False

    def begin_branch(self, name, rest, pop=True):
        ch = self.chains[-1]
        ch["branch"] += 1
        if pop and self.cond_stack:
            self.cond_stack.pop()
        if name == "else":
            text = "#else of " + ch["first"]
        else:
            text = "#elif %s of %s" % (squash(rest), ch["first"])
            if self.const_value(rest) is True:
                ch["taken"] = True
        c = Cond(ch["id"], ch["branch"], text)
        ch["bconds"].append(c)
        self.cond_stack.append(c)

    def end_chain(self, active_last=True):
        if not self.chains:
            return
        ch = self.chains.pop()
        if self.cond_stack:
            self.cond_stack.pop()
        states = list(ch["ends"]) + ([self.snapshot()] if active_last else [])
        if ch["ends"]:
            self.restore(ch["ends"][0])
        if ch["guard"] or len(states) < 2 or len(states) != len(ch["bconds"]) or any(c is None for c in ch["bconds"]):
            return
        base = states[0]
        if not base[1]:
            return
        alts = []
        for st, c in zip(states[1:], ch["bconds"][1:]):
            if st[1] and [id(f) for f in st[0]] == [id(f) for f in base[0]] and \
                    [id(t) for t in st[1]] != [id(t) for t in base[1]]:
                alts.append((c, list(st[1])))
        if alts:
            self.alts = [(ch["bconds"][0], None)] + alts
            self.alts_base = len(base[1])

    def on_pragma(self, rest):
        m = re.match(r"^pack\s*\((.*)\)\s*$", rest.strip())
        if not m:
            return
        args = [a.strip() for a in m.group(1).split(",") if a.strip()]
        if not args:
            self.pack = None
        elif args[0] == "push":
            self.pack_stack.append(self.pack)
            if args[-1].isdigit():
                self.pack = int(args[-1])
        elif args[0] == "pop":
            self.pack = self.pack_stack.pop() if self.pack_stack else None
        elif args[0].isdigit():
            self.pack = int(args[0])

    def find_guard(self):
        dirs = []
        first_tok = None
        for e in self.events:
            if e[0] == "dir":
                if e[2] not in ("pragma",):
                    dirs.append(e)
                if len(dirs) >= 2:
                    break
            elif first_tok is None:
                first_tok = e[1].line
        if len(dirs) < 2:
            return None
        d0, d1 = dirs[0], dirs[1]
        name = None
        if d0[2] == "ifndef":
            name = d0[3].split()[0] if d0[3].split() else None
        elif d0[2] == "if":
            m = re.match(r"^!\s*defined\s*\(?\s*(\w+)\s*\)?\s*$", d0[3])
            if m:
                name = m.group(1)
        if not name or d1[2] != "define" or (d1[3].split() or [""])[0] != name:
            return None
        if first_tok is not None and first_tok < d0[1]:
            return None
        return (d0[1], name)

    def cond_text(self, name, rest):
        rest = squash(rest)
        if name == "ifdef":
            return "#ifdef " + rest
        if name == "ifndef":
            return "#ifndef " + rest
        return "#if " + rest

    def const_value(self, rest):
        r = squash(rest)
        if r in ("0", "false", "(0)"):
            return False
        if r in ("1", "true", "(1)"):
            return True
        return None

    def snapshot(self):
        return (list(self.stack), list(self.pending), self.skip, self.skip_kind, self.skip_start,
                [(f, f.access, list(f.raw) if f.raw is not None else None,
                  list(f.done) if f.done is not None else None) for f in self.stack])

    def restore(self, snap):
        stack, pending, skip, skip_kind, skip_start, fstate = snap
        self.stack = list(stack)
        self.pending = list(pending)
        self.skip = skip
        self.skip_kind = skip_kind
        self.skip_start = skip_start
        for f, acc, raw, done in fstate:
            f.access = acc
            if raw is not None:
                f.raw = list(raw)
                f.done = list(done)

    def on_define(self, line, rest, endline):
        m = DEFINE_RE.match(rest)
        if not m:
            return
        self.flush_enum_raw()
        name, params, body = m.group(1), m.group(2), squash(m.group(3))
        self.macros.append({"name": name, "params": params, "body": body, "line": line,
                            "conds": self.cond_list(), "file": self.path})
        plist = None
        if params is not None:
            plist = [p.strip() for p in params[1:-1].split(",") if p.strip()]
        self.env[name] = (plist, body)
        self.local.add(name)

    def flush_enum_raw(self):
        fr = self.stack[-1]
        if fr.kind == "enum" and fr.raw:
            raw = []
            for part_i, part in enumerate(top_split(fr.raw)):
                if part_i:
                    raw.append(Tok("op", ",", part[0].line if part else 0, None, None, self.src, None))
                if len(part) == 1 and part[0].kind == "id" and part[0].text in self.local and \
                        self.env.get(part[0].text, (0, ""))[0] is None and self.env[part[0].text][1]:
                    raw.extend(synth_tokens(self.env[part[0].text][1], part[0]))
                else:
                    raw.extend(part)
            fr.done.extend(expand_macros(raw, self.env, self.local))
            fr.raw = []

    def pending_macro_use(self):
        p = self.pending
        return bool(p) and p[0].kind == "id" and is_macro_name(p[0].text) and \
            (p[0].text in GLOBAL_MACROS or p[0].text in self.env) and \
            (len(p) == 1 or (p[1].text == "(" and match_close(p, 1) == len(p) - 1))

    def on_include(self, line, rest):
        fr = self.stack[-1]
        m = re.match(r'^"([^"]+)"', rest.strip())
        if self.skip:
            return
        if self.pending_macro_use():
            self.flush(None)
        if not m or self.loader is None:
            if fr.kind in ("class", "enum"):
                self.notes.append("line %d: #include %s inside %s %s is not indexed" % (
                    line, rest.strip(), fr.kind, fr.name or "(anonymous)"))
            if self.pending:
                self.pending_cut = (line, rest.strip().strip('"<>'))
            return
        spelling = m.group(1)
        got = self.loader(self.path, spelling)
        if got is None:
            if fr.kind in ("class", "enum"):
                self.notes.append("line %d: #include \"%s\" inside %s %s is not indexed (not an in-repo file)" % (
                    line, spelling, fr.kind, fr.name or "(anonymous)"))
            if self.pending:
                self.pending_cut = (line, spelling)
            return
        inc_path, inc_text = got
        listy = not has_guard(inc_text) and (len(self.stack) > 1 or bool(self.pending))
        if fr.kind in ("class", "enum") or inc_path == self.path or inc_path.endswith(".def") or listy:
            self.splice(line, inc_path, inc_text)
        elif self.pending:
            self.pending_cut = (line, spelling)

    def splice(self, line, inc_path, inc_text):
        env = dict(self.env)
        local = set(self.local)
        toks = self.splice_tokens(inc_path, inc_text, env, 0, {self.path, inc_path}, local)
        out = toks
        if not out:
            return
        fr0 = self.stack[-1]
        own = [t.line for t in toks if t.src is not None and t.src.path == inc_path]
        span = ":%d-%d" % (min(own), max(own)) if own else ""
        where = ("%s %s" % (fr0.kind, fr0.name or "(anonymous)")) if fr0.kind in ("class", "enum") else \
            ("a declaration that starts at line %d" % self.pending[0].line if self.pending else "this file")
        self.notes.append("line %d: declarations spliced from %s%s, which is included inside %s" % (
            line, inc_path, span, where))
        fr = self.stack[-1]
        if fr.kind == "enum":
            self.flush_enum_raw()
            fr.done.extend(out)
            return
        for t in out:
            self.feed(t)

    def splice_tokens(self, inc_path, inc_text, env, depth, seen, local):
        src, events = lex(inc_path, inc_text)
        active = True
        stack = []
        toks = []
        buf = []
        for ev in events:
            if ev[0] == "dir":
                _, l2, name, rest, _e = ev
                if active and name in ("define", "undef", "include") and buf:
                    toks.extend(expand_macros(buf, env, local))
                    buf = []
                if name in ("if", "ifdef", "ifndef"):
                    v = pp_eval(name, rest, env) if active else False
                    stack.append((active, v))
                    active = active and v
                elif name == "elif":
                    if stack:
                        pa, taken = stack[-1]
                        v = (not taken) and pa and pp_eval("if", rest, env)
                        stack[-1] = (pa, taken or v)
                        active = pa and v
                elif name == "else":
                    if stack:
                        pa, taken = stack[-1]
                        stack[-1] = (pa, True)
                        active = pa and not taken
                elif name == "endif":
                    if stack:
                        active = stack.pop()[0]
                elif active and name == "define":
                    dm = DEFINE_RE.match(rest)
                    if dm:
                        pl = None
                        if dm.group(2) is not None:
                            pl = [p.strip() for p in dm.group(2)[1:-1].split(",") if p.strip()]
                        env[dm.group(1)] = (pl, squash(dm.group(3)))
                        local.add(dm.group(1))
                elif active and name == "undef":
                    env.pop((rest.split() or [""])[0], None)
                elif active and name == "include" and depth < 4 and self.loader is not None:
                    m = re.match(r'^"([^"]+)"', rest.strip())
                    got = self.loader(inc_path, m.group(1)) if m else None
                    if got is not None and got[0] not in seen and not has_guard(got[1]):
                        toks.extend(self.splice_tokens(got[0], got[1], env, depth + 1, seen | {got[0]}, local))
                continue
            if active:
                buf.append(ev[1])
        if buf:
            toks.extend(expand_macros(buf, env, local))
        return toks

    def feed(self, t):
        x = t.text
        fr = self.stack[-1]
        if self.skip:
            if x == "{":
                self.skip += 1
            elif x == "}":
                self.skip -= 1
                if self.skip == 0:
                    kind = self.skip_kind
                    self.skip_kind = None
                    if kind == "body":
                        self.flush(None, body=True)
                    elif kind == "init":
                        s = self.skip_start
                        self.pending.append(Tok("iblk", "{...}", s.line, s.start, t.end if t.src is s.src else
                                                s.end, s.src, s.ev))
            return
        if fr.kind == "enum":
            if x == "}":
                self.close_enum(t)
            else:
                fr.raw.append(t)
            return
        if self.pending and self.macro_boundary(t):
            self.flush(None)
        if self.seq is not None and x in ("{", "}"):
            if not self.pending:
                self.pending_conds = self.cond_list()
            self.pending.append(t)
            return
        if x == ";":
            if self.paren_depth() == 0:
                self.flush(t)
                return
        elif x == "{":
            if self.paren_depth() == 0:
                self.open_brace(t)
                return
        elif x == "}":
            if self.paren_depth() == 0:
                if self.pending:
                    self.flush(None)
                self.close_brace(t)
                return
        elif x == ":" and self.pending and self.pending[-1].text in ACCESS_WORDS and (
                len(self.pending) == 1 or self.pending[-2].text not in (":", ",", "virtual")):
            label = self.pending.pop()
            if self.pending:
                self.flush(None)
            if fr.kind == "class":
                fr.access = label.text
            elif not self.fragment:
                self.fragment = True
                self.notes.append("line %d: access label outside a class; this file is a class-body fragment "
                                  "(its members belong to the class that includes it)" % label.line)
            self.cur_file = label.src.path if label.src is not None else self.path
            self.add_decl("access", label.text, label.line, label.text + ":")
            return
        if not self.pending:
            self.pending_conds = self.cond_list()
        self.pending.append(t)

    def paren_depth(self):
        d = 0
        for t in self.pending:
            if t.text in ("(", "["):
                d += 1
            elif t.text in (")", "]"):
                d -= 1
        return d

    def macro_boundary(self, t):
        p = self.pending
        if p[0].kind != "id" or not is_macro_name(p[0].text):
            return False
        if t.line <= p[-1].line or t.src is not p[-1].src:
            return False
        if t.text in CONTINUATION:
            return False
        if len(p) == 1:
            if p[0].text not in GLOBAL_MACROS and p[0].text not in self.env:
                return False
        elif p[1].text != "(" or match_close(p, 1) != len(p) - 1:
            return False
        return not is_prefix_macro(p[0].text, self.env)

    def scope_name(self):
        parts = []
        for f in self.stack[1:]:
            if f.kind in ("namespace", "class") and f.name:
                parts.append(f.name)
        return "::".join(parts)

    def cur_class(self):
        for f in reversed(self.stack):
            if f.kind == "class":
                return f
            if f.kind == "namespace":
                return None
        return None

    def add_decl(self, kind, name, line, text, conds=None, file=None):
        fr = self.stack[-1]
        idx = len(self.stack) - 1
        while self.stack[idx].kind == "extern" and idx > 0:
            idx -= 1
        fr = self.stack[idx]
        parent = None
        for f in reversed(self.stack[:idx + 1]):
            if f.kind == "class" and f.decl is not None:
                parent = f.decl
                break
            if f.kind == "namespace":
                break
        access = fr.access if fr.kind == "class" else None
        self.counter += 1
        if file is None:
            file = self.cur_file or self.path
        d = Decl(kind, name, line, text, access, conds if conds is not None else self.cond_list(), parent,
                 self.scope_name(), file, self.counter)
        if self.gen_from is not None:
            d.extra["gen"] = self.gen_from
        if any(f.kind == "extern" and f.name == "C" for f in self.stack):
            d.extra["linkage"] = "C"
        if self.pack is not None and kind in ("class", "struct", "union"):
            d.extra["pack"] = self.pack
        if parent is not None:
            parent.members.append(d)
        else:
            self.decls.append(d)
        return d

    def resync(self):
        p = self.pending
        depth = 0
        for k in range(len(p)):
            x = p[k].text
            if x in ("(", "["):
                depth += 1
            elif x in (")", "]"):
                depth -= 1
            if k == 0 or depth != 0:
                continue
            if x == "namespace" and p[k - 1].text != "using":
                split = True
            elif x in CLASS_KEYS or x == "enum":
                pre = p[:k]
                split = pre[0].kind == "id" and is_macro_name(pre[0].text) and \
                    not is_prefix_macro(pre[0].text, self.env) and \
                    (len(pre) == 1 or (pre[1].text == "(" and match_close(pre, 1) == len(pre) - 1))
            else:
                continue
            if split:
                conds = self.pending_conds
                self.pending = p[:k]
                self.flush(None)
                self.pending = p[k:]
                self.pending_conds = conds
                return
            return

    def open_brace(self, t):
        self.resync()
        p = self.pending
        if not p:
            self.skip = 1
            self.skip_kind = "stray"
            self.skip_start = t
            return
        i = template_prefix(p)
        tp = p[:i]
        lead = p[skip_attrs(p[i:], 0) + i:]
        if lead and (lead[0].text in ("namespace", "extern") or self.class_head(lead) is not None):
            self.alts = None
        if lead and lead[0].text == "namespace":
            names = [x.text for x in lead[1:] if x.kind == "id" and x.text not in ATTR_WORDS]
            if len(lead) > 1 and lead[1].text in ATTR_WORDS:
                names = [x.text for x in lead[skip_attrs(lead, 1):] if x.kind == "id"]
            parts = "::".join(names).split("::") if names else ["(anonymous)"]
            for k, part in enumerate(parts):
                nf = Frame("namespace", part, None, None, [])
                nf.count = len(parts) if k == 0 else 0
                self.stack.append(nf)
            self.stack[-1].count = len(parts)
            self.pending = []
            return
        if lead and lead[0].text == "inline" and len(lead) > 1 and lead[1].text == "namespace":
            names = [x.text for x in lead[2:] if x.kind == "id"]
            self.stack.append(Frame("namespace", names[0] if names else "(anonymous)", None, None, []))
            self.pending = []
            return
        if lead and lead[0].text == "extern" and len(lead) == 2 and lead[1].kind == "lit":
            self.stack.append(Frame("extern", lead[1].text.strip('"'), None, None, []))
            self.pending = []
            return
        head = self.class_head(lead)
        if head is not None:
            key, name, bases, is_enum, under, name_line = head
            self.cur_file = p[0].src.path if p[0].src is not None else self.path
            conds = self.cond_list()
            text = render(p)
            if is_enum:
                d = self.add_decl("enum", name, name_line, text, conds=conds)
                d.extra.update({"under": under, "values": [], "scoped": len(lead) > 1 and lead[1].text in
                                ("class", "struct"), "template": None})
                nf = Frame("enum", name, None, d, p)
            else:
                d = self.add_decl(key, name, name_line, text, conds=conds)
                d.extra.update({"template": render(tp) if tp else None, "bases": bases,
                                "specialization": is_specialization(name, render(tp) if tp else "")})
                nf = Frame("class", name or "", "private" if key == "class" else "public", d, p)
            nf.head = list(p) + [t]
            self.stack.append(nf)
            self.pending = []
            return
        if self.is_initializer(p):
            self.skip = 1
            self.skip_kind = "init"
            self.skip_start = t
            return
        self.skip = 1
        self.skip_kind = "body"
        self.skip_start = t

    def class_head(self, lead):
        i = 0
        n = len(lead)
        while i < n and lead[i].text in ("typedef", "static", "const", "constexpr", "inline", "extern",
                                          "volatile", "thread_local", "friend"):
            i += 1
        if i >= n:
            return None
        key = lead[i].text
        if key not in CLASS_KEYS and key != "enum":
            return None
        is_enum = key == "enum"
        i += 1
        if is_enum and i < n and lead[i].text in ("class", "struct"):
            i += 1
        names = []
        name_line = lead[0].line
        while i < n:
            x = lead[i]
            if x.text in ATTR_WORDS or (x.text == "[" and i + 1 < n and lead[i + 1].text == "["):
                k = skip_attrs(lead, i)
                if k == i:
                    return None
                i = k
                continue
            if x.kind == "id" and x.text != "final":
                if i + 1 < n and lead[i + 1].text == "(" and is_macro_name(x.text):
                    j = match_close(lead, i + 1)
                    if j is None:
                        return None
                    i = j + 1
                    continue
                nm = x.text
                nl = x.line
                i += 1
                while i < n and lead[i].text in ("::", "<"):
                    if lead[i].text == "::" and i + 1 < n and lead[i + 1].kind == "id":
                        nm += "::" + lead[i + 1].text
                        i += 2
                    elif lead[i].text == "<":
                        j = match_close(lead, i)
                        if j is None:
                            return None
                        nm += render(lead[i:j + 1])
                        i = j + 1
                    else:
                        break
                names.append((nm, nl))
                continue
            if x.text == "final":
                i += 1
                continue
            break
        if i < n and lead[i].text != ":":
            return None
        if len(names) > 1 and any(not is_macro_name(nm) and "::" not in nm for nm, _ in names[:-1]):
            return None
        name, name_line = names[-1] if names else ("", lead[0].line)
        bases = None
        under = None
        if i < n and lead[i].text == ":" and i + 1 < n:
            s = render(lead[i + 1:])
            if is_enum:
                under = s
            else:
                bases = s
        return key, name, bases, is_enum, under, name_line

    def is_initializer(self, p):
        last = p[-1].text
        if last in ("=", ",", "(", "return"):
            return True
        i = template_prefix(p)
        q = p[i:]
        if not q:
            return False
        fp = self.find_params(q)
        if fp is not None:
            close = match_close(q, fp[0])
            depth = 0
            colon = False
            for x in q[close + 1:]:
                if x.text in ("(", "["):
                    depth += 1
                elif x.text in (")", "]"):
                    depth -= 1
                elif x.text == ":" and depth == 0:
                    colon = True
                    break
            if colon and (q[-1].kind == "id" or q[-1].text in (">", ">>")):
                return True
            return False
        if is_macro_name(q[0].text) and (len(q) == 1 or (q[1].text == "(" and match_close(q, 1) is not None)):
            j = 0 if len(q) == 1 else match_close(q, 1)
            if all(x.text in TAIL_WORDS for x in q[j + 1:]):
                return False
        if q[-1].kind == "id" or q[-1].text in ("]", ">", ">>"):
            return True
        return False

    def close_enum(self, t):
        self.flush_enum_raw()
        fr = self.stack.pop()
        d = fr.decl
        vals = []
        for part in top_split(fr.done):
            k = skip_attrs(part, 0)
            if k >= len(part) or part[k].kind != "id":
                continue
            nm = part[k].text
            val = None
            for j in range(k + 1, len(part)):
                if part[j].text == "=":
                    val = render(part[j + 1:]) if j + 1 < len(part) else ""
                    break
            vals.append((nm, val, part[k].line, part[k].src.path if part[k].src else self.path))
        d.extra["values"] = vals
        self.last_closed = d
        head = list(fr.head)
        brace = head.pop()
        self.pending = head + [Tok("tblk", "{...}", brace.line, brace.start, t.end, brace.src, brace.ev)]

    def close_brace(self, t):
        if len(self.stack) == 1:
            self.notes.append("line %d: '}' at file scope" % t.line)
            return
        fr = self.stack.pop()
        if fr.kind == "namespace" and fr.count > 1:
            for _ in range(fr.count - 1):
                if len(self.stack) > 1 and self.stack[-1].kind == "namespace":
                    self.stack.pop()
        if fr.kind == "class":
            head = list(fr.head)
            brace = head.pop()
            self.pending = head + [Tok("tblk", "{...}", brace.line, brace.start,
                                       t.end if t.src is brace.src else brace.end, brace.src, brace.ev)]
            self.last_closed = fr.decl
        else:
            self.pending = []

    def find_params(self, q):
        n = len(q)
        i = 0
        cands = []
        while i < n:
            x = q[i]
            if x.text in ATTR_WORDS and i + 1 < n and q[i + 1].text == "(":
                j = match_close(q, i + 1)
                if j is None:
                    return None
                i = j + 1
                continue
            if x.text == "[" and i + 1 < n and q[i + 1].text == "[":
                j = match_close(q, i)
                if j is None:
                    return None
                i = j + 1
                continue
            if x.text == "<" and angle_opens(q, i):
                j = match_close(q, i)
                if j is not None:
                    i = j + 1
                    continue
            if x.text == "operator":
                j = i + 1
                if j < n and q[j].text == "(" and j + 1 < n and q[j + 1].text == ")":
                    j += 2
                elif j < n and q[j].text in ("new", "delete"):
                    j += 1
                    if j + 1 < n and q[j].text == "[" and q[j + 1].text == "]":
                        j += 2
                elif j < n and q[j].text == "[" and j + 1 < n and q[j + 1].text == "]":
                    j += 2
                else:
                    while j < n and q[j].text != "(":
                        j += 1
                if j < n and q[j].text == "(":
                    cands.append((j, i))
                    k = match_close(q, j)
                    if k is None:
                        return None
                    i = k + 1
                    continue
                return None
            if x.text == "(":
                prev = q[i - 1] if i > 0 else None
                ok = prev is not None and (
                    (prev.kind == "id" and prev.text not in NOT_CALLEE and prev.text not in BUILTIN_TYPES
                     and prev.text not in SPECIFIERS and prev.text not in CLASS_KEYS)
                    or prev.text in (">", ">>"))
                j = match_close(q, i)
                if j is None:
                    return None
                if ok:
                    cands.append((i, None))
                i = j + 1
                continue
            if x.text in ("[", "{"):
                j = match_close(q, i)
                if j is None:
                    return None
                i = j + 1
                continue
            if x.text == "=" and not cands:
                return None
            i += 1
        for ci, op in cands:
            j = match_close(q, ci)
            if self.qualifier_tail(q, j + 1):
                return (ci, op)
        return None

    def qualifier_tail(self, q, i):
        n = len(q)
        while i < n:
            x = q[i].text
            if x == "->":
                return True
            if x == ":":
                return True
            if x == "=":
                return i + 1 < n and q[i + 1].text in ("0", "default", "delete") and i + 2 == n
            if x in ("noexcept", "throw", "__attribute__", "alignas", "__asm__", "asm", "__asm", "requires") \
                    and i + 1 < n and q[i + 1].text == "(":
                j = match_close(q, i + 1)
                if j is None:
                    return False
                i = j + 1
                continue
            if x == "[" and i + 1 < n and q[i + 1].text == "[":
                j = match_close(q, i)
                if j is None:
                    return False
                i = j + 1
                continue
            if x in TAIL_WORDS:
                i += 1
                continue
            if q[i].kind == "id" and is_macro_name(x):
                if i + 1 < n and q[i + 1].text == "(":
                    j = match_close(q, i + 1)
                    if j is None:
                        return False
                    i = j + 1
                else:
                    i += 1
                continue
            if q[i].kind == "lit" and i > 0 and q[i - 1].text in ("__asm__", "asm"):
                i += 1
                continue
            return False
        return True

    def flush(self, semi, body=False):
        p = self.pending
        self.pending = []
        if self.seq is not None and not self.in_alt:
            self.seq_take(p, semi)
            return
        if not p:
            return
        if self.pending_cut is not None:
            cut_line, spelling = self.pending_cut
            self.pending_cut = None
            self.pending_conds = None
            self.alts = None
            self.cut_decls += 1
            self.notes.append("line %d: the declaration that starts here is not listed: #include \"%s\" at line %d "
                              "sits inside it and is not an X-macro list" % (p[0].line, spelling, cut_line))
            return
        if self.alts is not None and not self.in_alt:
            alts = self.alts
            self.alts = None
            if len(p) >= self.alts_base:
                conds0 = self.pending_conds if self.pending_conds is not None else self.cond_list()
                suffix = p[self.alts_base:]
                for c, toks in alts[1:]:
                    self.in_alt = True
                    try:
                        self.pending = toks + suffix
                        self.pending_conds = conds0 + [c]
                        self.flush(semi, body)
                    finally:
                        self.in_alt = False
                        self.pending = []
                self.pending_conds = conds0 + [alts[0][0]]
        conds = self.pending_conds if self.pending_conds is not None else self.cond_list()
        self.pending_conds = None
        self.cur_file = p[0].src.path if p[0].src is not None else self.path
        i = template_prefix(p)
        tp = p[:i]
        q = p[i:]
        k = skip_attrs(q, 0)
        q2 = q[k:]
        if not q2:
            return
        first = q2[0].text
        text = render(p)
        if first in ("static_assert", ";") or (first == "_Static_assert"):
            return
        if first == "using":
            if len(q2) > 1 and q2[1].text == "namespace":
                return
            eq = next((j for j, x in enumerate(q2) if x.text == "="), None)
            if eq is not None and eq > 1:
                self.add_decl("alias", q2[1].text, q2[1].line, text, conds=conds)
            else:
                nm = q2[-1].text if q2[-1].kind == "id" else text
                self.add_decl("using", nm, q2[0].line, text, conds=conds)
            return
        if first == "friend":
            self.add_decl("friend", "", q2[0].line, text, conds=conds)
            return
        if first == "namespace":
            self.add_decl("alias", q2[1].text if len(q2) > 1 else "", q2[0].line, text, conds=conds)
            return
        if first == "typedef":
            nm, ln = self.typedef_name(q2)
            self.add_decl("typedef", nm, ln, text, conds=conds)
            return
        if first == "template" and not tp:
            self.skipped_defs += 1
            return
        if first == "extern" and len(q2) > 1 and q2[1].text == "template":
            return
        if not body and first in CLASS_KEYS | {"enum"} and self.is_forward(q2):
            self.forward += 1
            return
        if self.type_def_only(q2):
            blk = next(j for j, x in enumerate(q2) if x.kind == "tblk")
            tail = q2[blk + 1:]
            if tail and self.last_closed is not None and self.last_closed.text and not self.in_alt:
                self.last_closed.text += " {...} " + render(tail)
            return
        cls = self.cur_class()
        ks = 0
        while ks < len(q2) - 1 and q2[ks].text in ("inline", "static", "OB_INLINE", "virtual", "extern"):
            ks += 1
        if ks and (q2[ks].text in GLOBAL_MACROS or is_macro_name(q2[ks].text)) and self.is_macro_use(q2[ks:], cls):
            q2 = q2[ks:]
            first = q2[0].text
        if first in GLOBAL_MACROS or is_macro_name(first):
            if self.is_macro_use(q2, cls):
                if any(c.text in ("#ifdef " + first, "#if defined(" + first + ")", "#if defined " + first)
                       for c in conds):
                    self.list_entries[first] += 1
                    return
                self.add_decl("macro-use", first, q2[0].line, text + (" {...}" if body else ""), conds=conds)
                if not body and self.gen_from is None:
                    self.try_expand(q2, first, semi)
                return
        fp = self.find_params(q2)
        if fp is not None:
            self.function(p, tp, q2, fp, conds, body)
            return
        if body:
            self.add_decl("other", "", q2[0].line, text + " {...}", conds=conds)
            return
        self.variables(p, q2, conds)

    def expand_all(self, toks):
        env = dict(GLOBAL_MACROS)
        env.update(self.env)
        toks = list(toks)
        seen = set()
        while toks and toks[0].kind == "id" and toks[0].text in env and env[toks[0].text][0] is None and \
                env[toks[0].text][1] and toks[0].text not in seen:
            seen.add(toks[0].text)
            toks = synth_tokens(env[toks[0].text][1], toks[0]) + toks[1:]
        return expand_macros(toks, env, set(env))

    def try_expand(self, q2, name, semi):
        toks = list(q2) + ([semi] if semi is not None else [])
        out = self.expand_all(toks)
        if not out or [t.text for t in out] == [t.text for t in toks]:
            return
        if SCOPE_WRAPPER_RE.fullmatch(" ".join(t.text for t in out)):
            self.feed_generated(out, name)
            return
        depth = 0
        braces = 0
        for t in out:
            if t.text in ("{", "(", "["):
                depth += 1
            elif t.text in ("}", ")", "]"):
                depth -= 1
                if depth < 0:
                    return
            if t.text == "{":
                braces += 1
            elif t.text == "}":
                braces -= 1
        if depth == 0:
            if declares(out):
                self.feed_generated(out, name)
            return
        if braces > 0 and depth == braces:
            self.seq = {"name": name, "toks": out, "depth": braces, "line": q2[0].line}

    def seq_take(self, p, semi):
        sq = self.seq
        if p and p[0].kind == "id" and (p[0].text in GLOBAL_MACROS or p[0].text in self.env) and \
                self.is_macro_use(p, None):
            self.add_decl("macro-use", p[0].text, p[0].line, render(p), conds=self.cond_list())
        out = self.expand_all(p + ([semi] if semi is not None else []))
        sq["toks"].extend(out)
        for t in out:
            if t.text == "{":
                sq["depth"] += 1
            elif t.text == "}":
                sq["depth"] -= 1
        if sq["depth"] > 0 and len(sq["toks"]) < 50000:
            return
        self.seq = None
        if sq["depth"] == 0:
            self.feed_generated(sq["toks"], sq["name"])
        else:
            self.notes.append("line %d: the declarations %s opens are not listed: its braces do not close" % (
                sq["line"], sq["name"]))

    def feed_generated(self, out, name):
        saved = (self.pending, self.pending_conds)
        self.pending = []
        self.pending_conds = None
        self.gen_from = name
        try:
            for t in out:
                self.feed(t)
            if self.pending:
                self.flush(None)
        finally:
            self.gen_from = None
            self.pending, self.pending_conds = saved

    def is_forward(self, q2):
        n = len(q2)
        if any(x.kind in ("tblk", "iblk") for x in q2):
            return False
        i = 1
        if q2[0].text == "enum" and i < n and q2[i].text in ("class", "struct"):
            i += 1
        i = skip_attrs(q2, i)
        if i >= n or q2[i].kind != "id":
            return False
        i += 1
        while i + 1 < n and q2[i].text == "::" and q2[i + 1].kind == "id":
            i += 2
        if i < n and q2[i].text == ":" and q2[0].text == "enum":
            return True
        return i == n

    def type_def_only(self, q2):
        blk = None
        for j, x in enumerate(q2):
            if x.kind == "tblk":
                blk = j
                break
        if blk is None or q2[0].text == "typedef":
            return False
        k = 0
        while k < blk and q2[k].text in ("static", "const", "inline", "extern", "constexpr"):
            k += 1
        if q2[k].text not in CLASS_KEYS | {"enum"}:
            return False
        j = blk + 1
        n = len(q2)
        while j < n:
            x = q2[j]
            if x.text in ATTR_WORDS and j + 1 < n and q2[j + 1].text == "(":
                m = match_close(q2, j + 1)
                if m is None:
                    return False
                j = m + 1
                continue
            if x.kind == "id" and is_macro_name(x.text):
                if j + 1 < n and q2[j + 1].text == "(":
                    m = match_close(q2, j + 1)
                    if m is None:
                        return False
                    j = m + 1
                else:
                    j += 1
                continue
            return False
        return True

    def is_macro_use(self, q2, cls):
        n = len(q2)
        if cls is not None and q2[0].text == (split_qualified(cls.name) or [cls.name])[-1].split("<")[0]:
            return False
        if n == 1:
            return True
        if q2[1].text == "(":
            j = match_close(q2, 1)
            if j is None:
                return True
            rest = q2[j + 1:]
            return all(x.text in TAIL_WORDS or x.kind in ("tblk", "iblk") for x in rest)
        return all(x.text in TAIL_WORDS for x in q2[1:])

    def typedef_name(self, q2):
        n = len(q2)
        for j in range(n - 1):
            if q2[j].text == "(" and q2[j + 1].kind == "id":
                k = j + 1
                while k + 1 < n and q2[k].kind == "id" and q2[k + 1].text == "::":
                    k += 2
                if k > j + 1 and k + 1 < n and q2[k].text == "*" and q2[k + 1].kind == "id":
                    return q2[k + 1].text, q2[k + 1].line
            if q2[j].text == "(" and q2[j + 1].text in ("*", "&", "^"):
                k = j + 1
                while k < n and q2[k].text in ("*", "&", "^", "const", "volatile", "__cdecl"):
                    k += 1
                while k + 1 < n and q2[k].kind == "id" and q2[k + 1].text == "::":
                    k += 2
                if k < n and q2[k].kind == "id":
                    return q2[k].text, q2[k].line
        k = n - 1
        while k > 0:
            x = q2[k]
            if x.text in ("]", ")"):
                open_t = "[" if x.text == "]" else "("
                depth = 0
                while k > 0:
                    if q2[k].text == x.text:
                        depth += 1
                    elif q2[k].text == open_t:
                        depth -= 1
                        if depth == 0:
                            break
                    k -= 1
                k -= 1
                continue
            if x.kind == "id" and x.text not in ATTR_WORDS and not (is_macro_name(x.text) and k + 1 < n
                                                                     and q2[k + 1].text == "("):
                return x.text, x.line
            k -= 1
        return q2[-1].text, q2[-1].line

    def function(self, p, tp, q2, fp, conds, body):
        ci, op = fp
        close = match_close(q2, ci)
        spec = False
        if op is not None:
            name = "operator" + "".join(x.text if x.kind != "id" else " " + x.text for x in q2[op + 1:ci]).rstrip()
            j = op
            name_line = q2[op].line
        else:
            j = ci - 1
            if q2[j].text in (">", ">>"):
                depth = 0
                while j >= 0:
                    if q2[j].text == ">":
                        depth += 1
                    elif q2[j].text == ">>":
                        depth += 2
                    elif q2[j].text == "<":
                        depth -= 1
                        if depth <= 0:
                            j -= 1
                            break
                    j -= 1
                spec = True
            name = q2[j].text
            name_line = q2[j].line
            if j > 0 and q2[j - 1].text == "~":
                name = "~" + name
                j -= 1
        qual = []
        k = j - 1
        while k >= 1 and q2[k].text == "::" and (q2[k - 1].kind == "id" or q2[k - 1].text in (">", ">>")):
            if q2[k - 1].kind == "id":
                qual.insert(0, q2[k - 1].text)
                k -= 2
            else:
                qual.insert(0, "<>")
                break
        cls = self.cur_class()
        if qual and q2[0].text != "friend" and not (cls is not None and qual == [cls.name]):
            self.skipped_defs += 1
            return
        tail_end = len(q2)
        for t_i in range(close + 1, len(q2)):
            if q2[t_i].text == ":" and body:
                tail_end = t_i
                break
        while tail_end > 0 and q2[tail_end - 1].kind in ("tblk", "iblk"):
            tail_end -= 1
        text = render(p[:len(p) - len(q2) + tail_end])
        kind = "function"
        if cls is not None:
            kind = "method"
            bare = name.split("<")[0]
            last = (split_qualified(cls.name) or [cls.name])[-1]
            if bare == last.split("<")[0]:
                kind = "constructor"
            elif bare.startswith("~"):
                kind = "destructor"
        d = self.add_decl(kind, name, name_line, text, conds=conds)
        tail = [x.text for x in q2[close + 1:tail_end]]
        ret = " ".join(x.text for x in p[:len(p) - len(q2)] + q2[:j] if x.text not in SPECIFIERS)
        d.extra.update({"params": self.param_key(q2[ci + 1:close]), "body": body, "template": bool(tp), "ret": ret,
                        "spec": spec and tp and render(tp).replace(" ", "") == "template<>",
                        "deleted": tail[-2:] == ["=", "delete"], "operator": op is not None,
                        "cv": tuple(x for x in tail if x in ("const", "volatile", "&", "&&"))})

    def param_key(self, params):
        out = []
        for part in top_split(params):
            toks = list(part)
            depth = 0
            for j, t in enumerate(toks):
                if t.text in ("(", "[", "<"):
                    depth += 1
                elif t.text in (")", "]", ">"):
                    depth -= 1
                elif t.text == "=" and depth == 0:
                    toks = toks[:j]
                    break
            words = [t.text for t in toks]
            if len(toks) >= 2 and toks[-1].kind == "id" and toks[-1].text not in BUILTIN_TYPES \
                    and toks[-1].text not in ("const", "volatile") and toks[-2].text != "::":
                if any(w not in ("const", "volatile", "struct", "class", "enum", "typename") for w in words[:-1]):
                    words = words[:-1]
            out.append(" ".join(words))
        if out in ([""], ["void"]):
            return ()
        return tuple(out)

    def variables(self, p, q2, conds):
        blk = next((j for j, x in enumerate(q2) if x.kind == "tblk"), None)
        if blk is not None:
            parts = decl_split(q2[blk + 1:])
            names = []
            for part in parts:
                n2, l2 = self.var_name(part, allow_first=True)
                if n2:
                    names.append((n2, l2))
            fr = self.stack[-1]
            kind = "field" if fr.kind == "class" else "variable"
            for n2, l2 in names:
                self.add_decl(kind, n2, l2, render(p), conds=conds)
            if names:
                return
        parts = decl_split(q2)
        nm, ln = self.var_name(parts[0])
        if nm is None:
            if self.unnamed_bitfield(q2):
                return
            self.add_decl("other", "", q2[0].line, render(p), conds=conds)
            return
        fr = self.stack[-1]
        words = {x.text for x in parts[0]}
        has_init = "=" in words or any(x.kind == "iblk" for x in parts[0])
        is_const = ("constexpr" in words) or ("const" in words and has_init)
        in_class = fr.kind == "class" or (fr.kind == "extern" and self.cur_class() is not None)
        if in_class:
            kind = "field"
            if "static" in words:
                kind = "static-field"
            if is_const and ("static" in words or "constexpr" in words):
                kind = "constant"
        else:
            kind = "constant" if is_const else "variable"
        text = render(p)
        self.add_decl(kind, nm, ln, text, conds=conds)
        for extra in parts[1:]:
            n2, l2 = self.var_name(extra, allow_first=True)
            if n2:
                self.add_decl(kind, n2, l2, text, conds=conds)

    def unnamed_bitfield(self, q2):
        return len(q2) >= 3 and q2[-2].text == ":" and all(x.kind == "id" for x in q2[:-2])

    def var_name(self, toks, allow_first=False):
        n = len(toks)
        depth = 0
        cut = n
        for j, t in enumerate(toks):
            x = t.text
            if x == "(" and depth == 0 and j + 1 < n and toks[j + 1].kind == "id":
                k = j + 1
                while k + 1 < n and toks[k].kind == "id" and toks[k + 1].text == "::":
                    k += 2
                if k < n and toks[k].text == "*" and k > j + 1:
                    k += 1
                    if k < n and toks[k].kind == "id":
                        return toks[k].text, toks[k].line
            if x == "(" and depth == 0 and j + 1 < n and toks[j + 1].text in ("*", "&", "^"):
                k = j + 1
                while k < n and toks[k].text in ("*", "&", "^", "const", "volatile"):
                    k += 1
                while k + 1 < n and toks[k].kind == "id" and toks[k + 1].text == "::":
                    k += 2
                if k < n and toks[k].kind == "id":
                    return toks[k].text, toks[k].line
            if x in ("(", "[", "{"):
                if x == "[" and depth == 0:
                    cut = j
                    break
                depth += 1
            elif x in (")", "]", "}"):
                depth -= 1
            elif depth == 0 and x in ("=", ":"):
                cut = j
                break
            elif depth == 0 and t.kind == "iblk":
                cut = j
                break
        k = cut - 1
        while k >= 0:
            t = toks[k]
            if t.text == ")":
                depth = 0
                while k >= 0:
                    if toks[k].text == ")":
                        depth += 1
                    elif toks[k].text == "(":
                        depth -= 1
                        if depth == 0:
                            break
                    k -= 1
                k -= 1
                continue
            if t.kind == "id" and t.text not in SPECIFIERS and t.text not in BUILTIN_TYPES \
                    and t.text not in ATTR_WORDS and t.text not in CLASS_KEYS:
                if k == 0 and not allow_first:
                    return None, None
                if is_macro_name(t.text) and GLOBAL_MACROS.get(t.text) is not None and k > 0 and \
                        is_prefix_macro(t.text):
                    k -= 1
                    continue
                return t.text, t.line
            k -= 1
        return None, None


def rust_ident(name):
    if name in RUST_UNDERSCORE:
        return name + "_"
    if name in RUST_KEYWORDS:
        return "r#" + name
    return name


def exclusive(a, b):
    ca = {c.chain: c.branch for c in a.conds}
    for c in b.conds:
        if c.chain in ca and ca[c.chain] != c.branch:
            return True
    return False


def assign_rust_names(decls_by_file):
    groups = defaultdict(list)

    def walk(d, owner):
        if d.kind in ("function", "method", "constructor"):
            ex = d.extra
            if not ex.get("operator") and not ex.get("spec") and not ex.get("deleted") and \
                    not d.name.startswith("operator"):
                key = owner + "|" + ("new" if d.kind == "constructor" else d.name)
                groups[key].append(d)
        if d.kind in ("class", "struct", "union"):
            for m in d.members:
                walk(m, "C:" + (d.scope + "::" if d.scope else "") + (d.name or "(anonymous)"))
            if d.name and (d.parent is not None or "::" in d.name):
                d.rust = nested_name(d)
        elif d.kind in ("typedef", "alias") and d.parent is not None and d.name and \
                d.parent.extra.get("specialization"):
            d.rust = rust_ident(d.name) if rust_ident(d.name) != d.name else None
            d.extra["assoc"] = True
        elif d.kind in ("enum", "typedef", "alias") and d.parent is not None and d.name:
            d.rust = nested_name(d)
        if d.kind in ("field", "static-field", "constant", "variable", "function", "method", "enum", "typedef",
                      "alias", "class", "struct", "union") and d.rust is None and d.name and \
                rust_ident(d.name) != d.name:
            d.rust = rust_ident(d.name)

    for f in sorted(decls_by_file):
        for d in decls_by_file[f]:
            walk(d, "M")
    for key, ds in groups.items():
        ds.sort(key=lambda d: (d.file, d.line, d.order))
        base = key.split("|", 1)[1]
        assigned = []
        for d in ds:
            n = 1
            while any(num == n and not exclusive(d, o) for o, num in assigned):
                n += 1
            assigned.append((d, n))
            if base == "new":
                d.rust = "new" if n == 1 else "new_%d" % n
            elif n > 1:
                d.rust = "%s_%d" % (base, n)
            elif rust_ident(base) != base:
                d.rust = rust_ident(base)


def norm_params(params):
    out = []
    for x in params or ():
        x = re.sub(r"\b\w+::", "", x)
        x = re.sub(r"\b(struct|class|enum|typename)\s+", "", x)
        if "*" not in x and "&" not in x:
            x = re.sub(r"\b(const|volatile)\b", "", x)
        out.append(re.sub(r"\s+", "", x))
    return tuple(out)


def base_list(bases):
    parts = []
    cur = ""
    depth = 0
    for ch in bases or "":
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
    out = []
    for x in parts:
        x = re.sub(r"<.*", "", x, flags=re.S).strip()
        prev = None
        while prev != x:
            prev = x
            x = re.sub(r"^(public|private|protected|virtual)\s+", "", x).strip()
        x = x.lstrip(":").strip()
        if x and re.match(r"^[A-Za-z_][\w:]*$", x):
            out.append(x)
    return out


def align_overrides(all_decls):
    classes = {}
    simple = defaultdict(set)
    every = []

    def walk(d):
        if d.kind in ("class", "struct", "union") and d.name:
            q = (d.scope + "::" if d.scope else "") + d.name.split("<")[0]
            classes.setdefault(q, d)
            simple[d.name.split("<")[0]].add(q)
            every.append(d)
        for m in d.members:
            walk(m)

    for d in all_decls:
        walk(d)

    def resolve(base, cd):
        parts = cd.scope.split("::") if cd.scope else []
        for i in range(len(parts), -1, -1):
            q = "::".join(parts[:i] + [base]) if i else base
            if q in classes:
                return classes[q]
        cands = simple.get(base.split("::")[-1], set())
        if len(cands) == 1:
            return classes[next(iter(cands))]
        return None

    done = {}

    def methods_of(cd, stack):
        key = id(cd)
        if key in done:
            return done[key]
        if key in stack:
            return {}
        inherited = {}
        for b in base_list(cd.extra.get("bases")):
            bd = resolve(b, cd)
            if bd is None or bd is cd:
                continue
            for k, v in methods_of(bd, stack | {key}).items():
                inherited.setdefault(k, v)
        taken = defaultdict(set)
        for m in cd.members:
            if m.kind != "method" or m.extra.get("operator") or m.name.startswith("operator"):
                continue
            k = (m.name, norm_params(m.extra.get("params")), m.extra.get("cv"))
            if k in inherited and (inherited[k][1] or "virtual" in m.text or "override" in m.text):
                rn = inherited[k][0]
                m.rust = None if rn == m.name else rn
                m.extra["override"] = True
                taken[m.name].add(rn)
        for name, used in taken.items():
            used = set(used)
            for m in cd.members:
                if m.kind != "method" or m.name != name or m.extra.get("override") or m.extra.get("deleted") or \
                        m.extra.get("spec"):
                    continue
                n = 1
                while True:
                    cand = (rust_ident(name) if n == 1 else "%s_%d" % (name, n))
                    if cand not in used:
                        break
                    n += 1
                used.add(cand)
                m.rust = None if cand == name else cand
        result = dict(inherited)
        for m in cd.members:
            if m.kind == "method" and not m.extra.get("operator") and not m.name.startswith("operator"):
                k = (m.name, norm_params(m.extra.get("params")), m.extra.get("cv"))
                virt = "virtual" in m.text or "override" in m.text or bool(m.extra.get("override"))
                result[k] = (m.rust or m.name, virt)
        done[key] = result
        return result

    for cd in every:
        methods_of(cd, frozenset())


def compute_names(index):
    WORKER["index"] = index
    WORKER["parsed"] = {}
    files = sorted({f for fs in index.units.values() for f in fs if f.endswith(HEADER_LIKE)} |
                   {g for g in index.generated if g.endswith(HEADER_LIKE)})
    by_owner = defaultdict(dict)
    for f in files:
        by_owner[index.unit_of.get(f) or f][f] = parse_file(f)["decls"]
    for owner in sorted(by_owner):
        assign_rust_names(by_owner[owner])
    align_overrides([d for f in files for d in parse_file(f)["decls"]])
    names = {}

    def collect(d, path):
        if d.rust:
            names[(path, d.order)] = d.rust
        for m in d.members:
            collect(m, path)

    for f in files:
        for d in parse_file(f)["decls"]:
            collect(d, f)
    WORKER["parsed"] = {}
    return names


RUST_NAME_RE = re.compile(r"^(?:r#)?[A-Za-z_][A-Za-z0-9_]*$")


def split_qualified(name):
    parts = []
    cur = ""
    depth = 0
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if depth == 0 and name.startswith("::", i):
            parts.append(cur)
            cur = ""
            i += 2
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return [x.strip() for x in parts if x.strip()]


def template_params(text):
    out = set()
    for m in re.finditer(r"template\s*<([^<>]*(?:<[^<>]*>[^<>]*)*)>", text or ""):
        for part in m.group(1).split(","):
            part = part.split("=")[0].strip()
            ids = re.findall(r"[A-Za-z_]\w*", part.replace("...", " "))
            if ids:
                out.add(ids[-1])
    return out


def ident_part(name, params):
    base, sep, args = name.partition("<")
    base = base.strip()
    if not sep:
        return base
    args = args.rsplit(">", 1)[0]
    words = [a.strip() for a in args.split(",")]
    if words and all(w in params for w in words):
        return base
    return base + "_" + re.sub(r"[^A-Za-z0-9_]+", "_", args).strip("_")


def is_specialization(name, template_text):
    parts = split_qualified(name or "")
    if not parts or "<" not in parts[-1]:
        return False
    return ident_part(parts[-1], template_params(template_text)) != parts[-1].split("<")[0].strip()


def nested_name(d):
    chain = []
    p = d
    while p is not None:
        chain.insert(0, p)
        p = p.parent
    parts = []
    for x in chain:
        params = template_params(x.extra.get("template") if isinstance(x.extra.get("template"), str) else "")
        for q in split_qualified(x.name or "anon"):
            parts.append(ident_part(q, params))
    return "_".join(parts)


def enum_value_notes(values):
    out = []
    last = None
    offset = 0
    for nm, val, line, src in values:
        if val is not None:
            m = INT_RE.match(val.replace(" ", ""))
            if m:
                last = int(m.group(2), 0) if not m.group(2).startswith(("0b", "0B")) else int(m.group(2)[2:], 2)
                if m.group(1):
                    last = -last
                offset = 0
                out.append((nm, val, None, line, src))
            else:
                last = ("expr", val)
                offset = 0
                out.append((nm, val, None, line, src))
            continue
        if last is None:
            last = 0
            offset = 0
            out.append((nm, None, "0", line, src))
            continue
        offset += 1
        if isinstance(last, tuple):
            out.append((nm, None, "(%s) + %d" % (last[1], offset), line, src))
        else:
            out.append((nm, None, str(last + offset), line, src))
    return out


def cond_note(conds):
    if not conds:
        return ""
    return "  // " + " && ".join(c.text for c in conds)


def decl_notes(d, names_path, mods_path=None):
    idx = WORKER.get("index")
    note = ""
    rust = NAMES.get((names_path, d.order))
    if rust:
        note += "  // rust: " + rust
    if d.kind in ("typedef", "alias") and d.parent is not None and d.parent.extra.get("specialization"):
        note += "  // an associated type of the trait impl for its specialization"
    if d.extra.get("gen"):
        note += "  // from " + d.extra["gen"]
    if d.extra.get("linkage") == "C" and d.kind in ("function", "variable", "constant"):
        note += "  // extern \"C\""
    if d.extra.get("pack"):
        note += "  // #pragma pack(%d)" % d.extra["pack"]
    if idx is not None and d.kind in ("function", "variable", "constant", "method"):
        for sym, b, dline in idx.links.get((d.file, d.line), ()):
            if sym.split("::")[-1] != d.name:
                continue
            where = idx.module_at(b, dline)
            if where is None:
                isl = idx.island_of(b)
                where = ("island %s, kept C" % isl) if isl else (idx.unit_target(idx.unit_of.get(b, ""))[0] or "-")
            note += "  // defined at %s:%d (%s)" % (b, dline, where)
    if mods_path is not None and idx is not None:
        m = idx.module_at(mods_path, d.line)
        if m:
            note += "  // in " + m
    note += cond_note(d.conds)
    return note


class Keep:
    __slots__ = ("ids", "enums")

    def __init__(self):
        self.ids = set()
        self.enums = {}


def shown_members(d, keep):
    if keep is None:
        return d.members
    out = []
    label = None
    for m in d.members:
        if m.kind == "access":
            label = m
            continue
        if id(m) in keep.ids:
            if label is not None:
                out.append(label)
                label = None
            out.append(m)
    return out


def render_decl(d, depth, lines, header_path, keep=None, names_path=None, mods_path=None):
    ind = "  " * depth
    loc = str(d.line) if d.file == header_path else "%s:%d" % (d.file, d.line)
    note = decl_notes(d, names_path or header_path, mods_path if depth == 0 else None)
    if d.kind == "access":
        lines.append("%s %s%s" % (loc, ind, d.text))
        return
    if d.kind in ("class", "struct", "union"):
        lines.append("%s %s%s%s" % (loc, ind, d.text, note))
        for m in shown_members(d, keep):
            render_decl(m, depth + 1, lines, header_path, keep, names_path)
        return
    if d.kind == "enum":
        lines.append("%s %s%s%s" % (loc, ind, d.text, note))
        values = enum_value_notes(d.extra.get("values", []))
        shown = values if keep is None or id(d) not in keep.enums else \
            [v for v in values if v[0] in keep.enums[id(d)]]
        for nm, val, computed, line, src in shown:
            eloc = str(line) if src == header_path else "%s:%d" % (src, line)
            if val is not None:
                lines.append("%s %s  %s = %s" % (eloc, ind, nm, val))
            else:
                lines.append("%s %s  %s  // = %s" % (eloc, ind, nm, computed))
        if len(shown) < len(values):
            lines.append("%s %s  // %d of %d enumerators listed: the ones the unit's files name" % (
                loc, ind, len(shown), len(values)))
        return
    text = d.text
    if d.kind == "macro-use" and len(text) > USE_TEXT_CAP:
        text = text[:USE_TEXT_CAP] + " ...(%d more chars)" % (len(d.text) - USE_TEXT_CAP)
    if d.kind == "other":
        note = "  // unparsed" + note
    end = ";" if d.kind not in ("macro-use", "other") and not text.endswith(";") else ""
    lines.append("%s %s%s%s%s" % (loc, ind, text, end, note))


def render_macro(m):
    params = m["params"] or ""
    body = m["body"]
    if len(body) > MACRO_TEXT_CAP:
        body = body[:MACRO_TEXT_CAP] + " ...(%d more chars)" % (len(m["body"]) - MACRO_TEXT_CAP)
    line = "#define %s%s%s" % (m["name"], params, (" " + body) if body else "")
    return line + cond_note(m["conds"])


class Index:
    def __init__(self, args):
        self.root = os.path.realpath(args.root)
        self.depmap = os.path.join(self.root, args.depmap)
        self.out = os.path.join(self.root, args.out)
        self.args = args
        self.load_units()
        self.load_edges()
        self.load_forwarders()
        self.load_generated()
        self.load_aliases()
        self.load_roots()
        self.load_crates()
        self.load_islands()
        self.load_manifest()
        self.closure_cache = {}
        self.text_cache = {}

    def read_tsv(self, name):
        rows = []
        with open(os.path.join(self.depmap, name), encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0 or line.startswith("#"):
                    continue
                rows.append(line.rstrip("\n").split("\t"))
        return rows

    def load_units(self):
        self.units = {}
        self.in_build = {}
        self.unit_of = {}
        for row in self.read_tsv("units.tsv"):
            files = row[1].split(",")
            self.units[row[0]] = files
            self.in_build[row[0]] = row[4] if len(row) > 4 else ""
            for f in files:
                self.unit_of[f] = row[0]

    def load_edges(self):
        self.includes = defaultdict(list)
        self.defines = defaultdict(set)
        self.links = defaultdict(list)
        for row in self.read_tsv("edges.tsv"):
            a, b, kind, ev = row[0], row[1], row[2], row[3] if len(row) > 3 else ""
            if kind in ("include", "definition"):
                parts = [(kind, ev)]
            elif kind == "link":
                parts = [(kind, ev)]
            elif kind in ("island", "from-island"):
                parts = []
                for seg in ev.split("; "):
                    k, _, rest = seg.partition(" ")
                    parts.append((k, rest))
            else:
                continue
            for k, rest in parts:
                if k == "include":
                    m = EDGE_LINE_RE.match(rest)
                    line = int(m.group(1)) if m else 0
                    cond = m.group(2) if m and m.group(2) else ""
                    self.includes[a].append((b, line, cond))
                elif k == "definition":
                    self.defines[a].add(b)
                elif k == "link":
                    for seg in rest.split("; "):
                        m = LINK_EV_RE.match(seg)
                        if m:
                            self.links[(a, int(m.group(1)))].append((m.group(2), b, int(m.group(3))))

    def load_forwarders(self):
        self.forwarders = {}
        for row in self.read_tsv("forwarders.tsv"):
            self.forwarders[row[0]] = row[1].split(",")

    def load_generated(self):
        self.generated = set()
        self.gen_info = {}
        for row in self.read_tsv("generated.txt"):
            if row and not row[0].startswith("build_") and os.path.isfile(os.path.join(self.root, row[0])):
                self.generated.add(row[0])
                self.gen_info[row[0]] = (row[5] if len(row) > 5 else "", row[6] if len(row) > 6 else "")

    def load_aliases(self):
        self.alias_of = {}
        if os.path.isfile(os.path.join(self.depmap, "aliases.tsv")):
            for row in self.read_tsv("aliases.tsv"):
                if len(row) >= 3 and row[1] not in ("", "-"):
                    self.alias_of[row[0]] = (row[1], row[2])

    def load_roots(self):
        roots = []
        build = None
        summary = os.path.join(self.depmap, "summary.txt")
        if os.path.isfile(summary):
            with open(summary, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("reference build\t"):
                        build = line.split("\t", 1)[1].strip()
        if self.args.build:
            build = self.args.build
        if build and os.path.isfile(os.path.join(build, "compile_commands.json")):
            ref_root = os.path.dirname(os.path.realpath(build))
            with open(os.path.join(build, "compile_commands.json"), encoding="utf-8") as f:
                entries = json.load(f)
            counts = defaultdict(int)
            for e in entries:
                args = shlex.split(e["command"]) if "command" in e else e["arguments"]
                dirs = []
                for i, a in enumerate(args):
                    if a == "-I" and i + 1 < len(args):
                        dirs.append(args[i + 1])
                    elif a.startswith("-I") and len(a) > 2:
                        dirs.append(a[2:])
                rel = []
                for d in dirs:
                    d = os.path.normpath(os.path.join(e["directory"], d))
                    if d == ref_root:
                        rel.append("")
                    elif d.startswith(ref_root + "/"):
                        r = d[len(ref_root) + 1:]
                        if not r.startswith(("deps/", "build")) and os.path.isdir(os.path.join(self.root, r)):
                            rel.append(r)
                counts[tuple(rel)] += 1
            best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            for dirs, _ in best:
                for d in dirs:
                    if d not in roots:
                        roots.append(d)
        if not roots:
            roots = ["rust/sql-nio/include", "", "src", "src/query/api", "src/data_plane/api", "src/objit/include",
                     "src/oblib/easy", "src/oblib", "src/oblib/common", "src/oblib/easy/include"]
        self.roots = roots

    def load_crates(self):
        path = os.path.join(self.root, "migration", "crates.tsv")
        if not os.path.isfile(path):
            path = os.path.join(self.root, "migration", "design", "evidence", "rulebook", "crates",
                                "crates-design.tsv")
        self.crate_path = os.path.relpath(path, self.root)
        rules = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].rstrip("\n")
                if not line.strip():
                    continue
                cols = line.split("\t")
                rules.append((cols[0].rstrip("/"), cols[1].strip(), cols[2].strip() if len(cols) > 2 else ""))
        rules.sort(key=lambda r: -len(r[0]))
        self.crate_rules = rules
        dirs = set()
        for u, files in self.units.items():
            for f in files:
                parts = f.split("/")
                for i in range(1, len(parts)):
                    dirs.add("/".join(parts[:i]))
        self.dirs = dirs

    def load_islands(self):
        self.islands = []
        path = os.path.join(self.depmap, "islands.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) >= 2:
                        self.islands.append((cols[0], cols[1]))
        self.islands.sort(key=lambda r: -len(r[0]))

    def island_of(self, path):
        for pre, crate in self.islands:
            if path == pre or path.startswith(pre if pre.endswith(("/", ".", "_")) else pre + "/") or \
                    (not pre.endswith("/") and path.startswith(pre) and pre not in self.dirs):
                return None if crate == "-" else crate
        return None

    def load_manifest(self):
        self.manifest = {}
        self.pieces = defaultdict(list)
        self.spans = defaultdict(list)
        self.core = {}
        self.not_translated = {}
        self.part_reasons = defaultdict(list)
        mig = os.path.join(self.root, "migration")
        for name in ("manifest.tsv", "core-manifest.tsv"):
            path = os.path.join(mig, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    cols = line.rstrip("\n").split("\t")
                    if i == 0 or len(cols) < 5:
                        continue
                    target, unit_id, kind, inputs = cols[1], cols[2], cols[3], cols[4]
                    mod = self.module_of_target(target)[1]
                    for inp in inputs.split(","):
                        m = re.match(r"^(.*):(\d+)-(\d+)$", inp)
                        if m:
                            self.spans[m.group(1)].append((int(m.group(2)), int(m.group(3)), mod, unit_id, target))
                        elif inp and inp != "-":
                            self.spans[inp].append((1, 1 << 30, mod, unit_id, target))
                    if name == "core-manifest.tsv":
                        for u in (cols[5].split(",") if len(cols) > 5 else []):
                            if u and u not in self.core:
                                self.core[u] = (target, unit_id)
                        continue
                    base = re.sub(r"\.p\d+$", "", unit_id)
                    if unit_id == base:
                        self.manifest[unit_id] = target
                    if kind == "split":
                        for inp in inputs.split(","):
                            m = re.match(r"^(.*):(\d+)-(\d+)$", inp)
                            if m:
                                self.pieces[m.group(1)].append((int(m.group(2)), int(m.group(3)), target))
        path = os.path.join(mig, "not-translated.tsv")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    cols = line.rstrip("\n").split("\t")
                    if i == 0 or len(cols) < 3:
                        continue
                    if cols[2] == ",".join(self.units.get(cols[0], [])):
                        self.not_translated[cols[0]] = cols[1]
                    else:
                        self.part_reasons[cols[2].split(":")[0]].append((cols[2], cols[1], cols[3] if len(cols) > 3
                                                                         else ""))
        for k in self.pieces:
            self.pieces[k].sort()
        for k in self.spans:
            self.spans[k].sort()

    def module_at(self, path, line):
        for a, b, mod, uid, target in self.spans.get(path, ()):
            if a <= line <= b:
                return mod
        return None

    def file_modules(self, path):
        out = []
        for a, b, mod, uid, target in self.spans.get(path, ()):
            if mod not in out:
                out.append(mod)
        return out

    def file_text(self, path):
        t = self.text_cache.get(path)
        if t is None:
            try:
                with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                    t = f.read()
            except OSError:
                t = ""
            if len(self.text_cache) > 4000:
                self.text_cache.clear()
            self.text_cache[path] = t
        return t

    def generated_home(self, path):
        gen, plan = self.gen_info.get(path, ("", ""))
        pre, crate, dval = self.crate_match(path)
        if plan.startswith("regenerated") and crate and not crate.startswith("x-"):
            stem = os.path.splitext(os.path.basename(path))[0]
            return crate, "%s::generated::%s" % (crate.replace("-", "_"), stem), gen
        return None, None, gen or plan

    def module_of_target(self, t):
        m = re.match(r"^rust/([^/]+)/src/(.*)\.rs$", t)
        if not m:
            return None, t
        crate = m.group(1)
        parts = m.group(2).split("/")
        if parts[-1] in ("lib", "main", "mod"):
            parts = parts[:-1]
        mod = "::".join(("r#" + c if c in RUST_KEYWORDS else c) for c in parts)
        return crate, crate.replace("-", "_") + ("::" + mod if mod else "")

    def crate_match(self, f):
        for pre, crate, d in self.crate_rules:
            if f == pre or f.startswith(pre + "/") or (pre not in self.dirs and f.startswith(pre)):
                return pre, crate, d
        return None, None, None

    def unit_target(self, unit):
        files = self.units.get(unit, [])
        if unit in self.manifest:
            return self.module_of_target(self.manifest[unit])
        if unit in self.core:
            return self.module_of_target(self.core[unit][0])
        if unit in self.not_translated:
            pre, crate, dval = self.crate_match(files[0] if files else unit)
            return crate or "-", None
        source = None
        for ext in IMPL_EXTS:
            if unit + ext in files:
                source = unit + ext
                break
        if source is None:
            hs = [f for f in files if f.endswith(HEADER_LIKE)]
            source = hs[0] if hs else (files[0] if files else unit)
        pre, crate, dval = self.crate_match(source)
        if crate is None:
            return None, None
        if crate.startswith("x-") or crate in ("geo-sys", "vsag-sys", "sql-parser-sys"):
            return crate, None
        pdir = pre if pre in self.dirs else os.path.dirname(pre)
        d = os.path.dirname(source)
        rel = os.path.relpath(d, pdir) if d != pdir else ""
        rel = "" if rel == "." else rel
        parts = [x for x in ([dval] if dval else []) + (rel.split("/") if rel else []) if x]
        stem = os.path.basename(unit)
        if stem in ("lib", "main", "mod"):
            stem += "_"
        parts.append(stem)
        mod = "::".join(("r#" + c if c in RUST_KEYWORDS else c) for c in parts)
        return crate, crate.replace("-", "_") + "::" + mod

    def resolve_include(self, from_file, spelling, quoted=True):
        cands = []
        if quoted:
            cands.append(os.path.normpath(os.path.join(os.path.dirname(from_file), spelling)))
        for r in self.roots:
            cands.append(os.path.normpath(os.path.join(r, spelling)) if r else os.path.normpath(spelling))
        for c in cands:
            if c.startswith(".."):
                continue
            if os.path.isfile(os.path.join(self.root, c)):
                return c
        return None

    def loader(self, from_file, spelling):
        p = self.resolve_include(from_file, spelling)
        if p is None or (p not in self.unit_of and p not in self.generated):
            return None
        with open(os.path.join(self.root, p), encoding="utf-8", errors="replace") as f:
            return p, f.read()

    def generated_includes(self, f):
        cache = WORKER.setdefault("gen_inc", {})
        r = cache.get(f)
        if r is None:
            r = [b for b, line, cond in self.direct_generated(f)]
            cache[f] = r
        return r

    def direct_generated(self, f):
        out = []
        try:
            with open(os.path.join(self.root, f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return out
        for m in INCLUDE_LINE_RE.finditer(text):
            p = self.resolve_include(f, m.group(2), m.group(1) == '"')
            if p in self.generated:
                line = text.count("\n", 0, m.start()) + 1
                out.append((p, line, ""))
        return out

    def one_level(self, unit):
        files = self.units[unit]
        own = set(files)
        found = {}

        def add(target, how):
            if target in own:
                return
            found.setdefault(target, [])
            if how not in found[target]:
                found[target].append(how)

        def expand(target, how, seen):
            if target in self.forwarders and target not in seen:
                seen = seen | {target}
                for t in self.forwarders[target]:
                    expand(t, how + " via forwarder %s" % target, seen)
                return
            if target in self.alias_of and target not in seen:
                kept, guard = self.alias_of[target]
                expand(kept, how + " of %s, a copy of this header (include guard %s, aliases.tsv)" % (target, guard),
                       seen | {target})
                return
            add(target, how)

        source = None
        for ext in IMPL_EXTS:
            if unit + ext in files:
                source = unit + ext
                break
        if source is None:
            hs = [f for f in files if f.endswith(HEADER_LIKE) and f not in self.forwarders]
            source = hs[0] if hs else (files[0] if files else None)
        all_mods = []
        for f in files:
            if not self.island_of(f):
                for m in self.file_modules(f):
                    if m not in all_mods:
                        all_mods.append(m)
        if len(all_mods) > 1:
            for f in files:
                if f.endswith(IMPL_EXTS) or self.island_of(f) or f in self.forwarders or not self.file_modules(f):
                    continue
                found.setdefault(f, []).append("a file of this unit, whose rows write %d modules (%s); its "
                                               "declarations show the module each one is written to" % (
                                                   len(all_mods), ", ".join(all_mods)))
        for f in files:
            for b, line, cond in self.includes.get(f, []):
                how = "#include at %s:%d%s" % (f, line, (" under #if " + cond) if cond else "")
                expand(b, how, frozenset())
            for b, line, cond in self.direct_generated(f):
                expand(b, "#include at %s:%d (generated file)" % (f, line), frozenset())
            for b in sorted(self.defines.get(f, ())):
                if b not in own:
                    expand(b, "%s defines declarations of this file" % f, frozenset())
        return found


WORKER = {}


def worker_init(args, macros, names):
    GLOBAL_MACROS.update(macros)
    NAMES.update(names)
    WORKER["index"] = Index(args)
    WORKER["parsed"] = {}


def parse_file(path):
    parsed = WORKER["parsed"]
    if path in parsed:
        return parsed[path]
    idx = WORKER["index"]
    try:
        with open(os.path.join(idx.root, path), encoding="utf-8", errors="replace") as f:
            text = f.read()
        hp = HeaderParser(path, text, loader=idx.loader).run()
        res = {"decls": hp.decls, "macros": hp.macros, "notes": hp.notes, "forward": hp.forward,
               "skipped": hp.skipped_defs + hp.redeclared, "inactive": hp.inactive,
               "guards": sorted(hp.guard_names | ({hp.guard[1]} if hp.guard else set())),
               "lists": dict(hp.list_entries),
               "undefs": hp.undefs, "error": None, "lines": text.count("\n") + 1}
    except Exception as e:
        res = {"decls": [], "macros": [], "notes": [], "forward": 0, "skipped": 0, "inactive": 0, "guards": [],
               "lists": {},
               "undefs": {}, "error": "%s: %s" % (type(e).__name__, e), "lines": 0}
    parsed[path] = res
    return res


def merged_spans(idx, path, n_lines):
    out = []
    for a, b, mod, uid, target in idx.spans.get(path, ()):
        b = min(b, n_lines)
        if out and out[-1][2] == mod and out[-1][1] + 1 >= a:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b, mod])
    return out


def header_tags(path):
    idx = WORKER["index"]
    owner = idx.unit_of.get(path)
    res = parse_file(path)
    tags = []
    multi = False
    if owner:
        tags.append("unit " + owner)
        crate, mod = idx.unit_target(owner)
        island = idx.island_of(path)
        reason = idx.not_translated.get(owner)
        parts = idx.part_reasons.get(path, [])
        spans = merged_spans(idx, path, res.get("lines", 0) or 1)
        if island or reason == "island":
            tags.append("island %s: kept C/C++, reached through the island's C ABI" % (island or crate))
            if spans:
                tags.append("lines ported to Rust by a design placement: " + ", ".join(
                    "%d-%d in %s" % (a, b, m) for a, b, m in spans))
        elif reason:
            tags.append("crate %s; not translated (%s)" % (crate, reason))
        elif not spans and parts:
            tags.append("not translated (%s)" % "; ".join("%s: %s" % (r, rule) for _, r, rule in parts))
        elif spans:
            crates = []
            for a, b, m in spans:
                c = m.split("::")[0].replace("_", "-")
                if c not in crates:
                    crates.append(c)
            tags.append("crate " + ", ".join(crates))
            core_ids = sorted({uid for a, b, m, uid, t in idx.spans.get(path, ()) if uid.startswith("core/")})
            if core_ids:
                tags.append("core module " + ", ".join(core_ids))
            if len(spans) == 1 and spans[0][0] == 1 and not parts:
                tags.append("rust " + spans[0][2])
            else:
                tags.append("rust " + ", ".join("%s (lines %d-%d)" % (m, a, b) for a, b, m in spans))
                multi = len({m for _, _, m in spans}) > 1
            for rng, r, rule in parts:
                tags.append("%s not translated (%s)" % (rng, r))
        elif crate:
            tags.append("crate %s; no rust module" % crate)
    elif path in idx.generated:
        crate, mod, gen = idx.generated_home(path)
        if mod:
            tags.append("generated file; crate %s; rust %s, written by the Rust back end of %s "
                        "(s1-crates-core.md 5.5)" % (crate, mod, gen))
        else:
            tags.append("generated file; not translated (%s)" % gen)
    return tags, multi


def header_block(path, hows, keep=None):
    idx = WORKER["index"]
    owner = idx.unit_of.get(path)
    res = parse_file(path)
    lines = []
    tags, multi = header_tags(path)
    lines.append("== %s  [%s]" % (path, "; ".join(tags)))
    for h in hows:
        lines.append("   from: " + h)
    if res["error"]:
        lines.append("   error: could not parse: " + res["error"])
        return lines
    if keep is not None:
        lines.append("   note: only the declarations the unit's files name, and the types those declarations name, "
                     "are listed from this header")
    notes = []
    for n in res["notes"]:
        m = re.search(r"declarations spliced from (\S+?)(?::\d+-\d+)?, which is included inside", n)
        if m and idx.unit_of.get(m.group(1)) and idx.unit_of.get(m.group(1)) != owner:
            u = idx.unit_of[m.group(1)]
            um = idx.module_at(m.group(1), 1) or idx.unit_target(u)[1] or "no rust module"
            n += "; that file is unit %s (rust %s, which holds the list as macro_rules! over a Rust copy, RULEBOOK " \
                 "2.3), and the declarations it generates here belong to this header's module" % (u, um)
        notes.append(n)
    if res["forward"]:
        notes.append("%d forward declaration(s) not listed" % res["forward"])
    if res["skipped"]:
        notes.append("%d definition(s) of members or functions declared above not listed" % res["skipped"])
    if res["inactive"]:
        notes.append("%d line(s) under #if 0 not listed" % res["inactive"])
    for name, cnt in sorted(res["lists"].items()):
        notes.append("%d X-macro list entries %s(...) under #ifdef %s not listed; they are shown where the list is "
                     "expanded" % (cnt, name, name))
    ipps = []
    if keep is None and owner:
        for b, line, cond in idx.includes.get(path, []):
            if b.endswith(".ipp") and b != path and idx.unit_of.get(b) == owner and b not in ipps and \
                    not any(("spliced from " + b) in n for n in res["notes"]):
                ipps.append(b)
                notes.append("the types and free declarations of %s, which this header includes at line %d and which "
                             "belongs to the same unit, are listed here with their file" % (b, line))
    for n in notes:
        if keep is None:
            lines.append("   note: " + n)
    items = []
    local_undef = res["undefs"]
    messages = []
    for m in res["macros"]:
        if m["name"] in res["guards"]:
            continue
        if keep is not None and m["name"] not in keep.ids:
            continue
        if any(u > m["line"] for u in local_undef.get(m["name"], ())):
            continue
        if path in idx.generated and m["params"] is None and STRING_BODY_RE.match(m["body"]) and keep is None:
            messages.append(m["name"])
            continue
        items.append((m["line"], 0, "macro", m, path))
    if messages:
        lines.append("   note: %d object-like macros whose body is one string literal (message texts such as %s) not "
                     "listed; read them in the file" % (len(messages), messages[0]))
    for d in res["decls"]:
        if keep is None or id(d) in keep.ids:
            items.append((d.line, d.order, "decl", d, path))
    for b in ipps:
        for d in parse_file(b)["decls"]:
            if d.kind not in ("macro-use", "other", "access"):
                items.append((1 << 30, d.line * 100000 + d.order, "decl", d, b))
    items.sort(key=lambda x: (x[0], x[1]))
    scope = ""
    for line, _, kind, obj, src in items:
        if kind == "macro":
            note = ""
            if multi:
                mm = idx.module_at(path, line)
                note = ("  // in " + mm) if mm else ""
            lines.append("%d %s%s" % (line, render_macro(obj), note))
        else:
            if obj.scope != scope and obj.kind != "access":
                scope = obj.scope
                lines.append("-- namespace %s" % (scope or "(global)"))
            render_decl(obj, 0, lines, path, keep, names_path=src, mods_path=path if multi and src == path else None)
    return lines


IDENT_USE_RE = re.compile(r"[A-Za-z_]\w*")
STRING_LIT_RE = re.compile(r"""\bR"([^()\\\s"]{0,16})\(.*?\)\1"|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'""", re.S)


def used_names(text):
    return set(IDENT_USE_RE.findall(STRING_LIT_RE.sub(" ", strip_comments(text))))


def name_table():
    table = WORKER.get("global_names")
    if table is None:
        idx = WORKER["index"]
        table = defaultdict(list)
        for f in sorted({f for fs in idx.units.values() for f in fs if f.endswith(HEADER_LIKE)} |
                        {g for g in idx.generated if g.endswith(HEADER_LIKE)}):
            res = parse_file(f)
            for d in res["decls"]:
                if d.kind in ("macro-use", "other", "access", "friend", "using"):
                    continue
                if d.name:
                    table[d.name.split("<")[0].split("::")[-1]].append((f, d))
                if d.kind == "enum":
                    for v in d.extra.get("values", []):
                        table[v[0]].append((f, d))
            for m in res["macros"]:
                if m["name"] not in res["guards"]:
                    table[m["name"]].append((f, m))
        WORKER["global_names"] = table
    return table


def include_closure(idx, files):
    seen = set()
    stack = list(files)
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        for b, line, cond in idx.includes.get(f, ()):
            b = idx.alias_of.get(b, (b,))[0]
            if b not in seen:
                stack.append(b)
        for b in idx.generated_includes(f):
            if b not in seen:
                stack.append(b)
    return seen


TYPE_KINDS = ("class", "struct", "union", "enum", "typedef", "alias")


def select_referenced(unit, heads):
    idx = WORKER["index"]
    files = idx.units[unit]
    used = set()
    for f in files:
        used |= used_names(idx.file_text(f))
    closure = include_closure(idx, files) - set(files) - set(heads)
    closure = {h for h in closure if idx.island_of(h) or h in idx.generated or
               (idx.unit_of.get(h) not in idx.not_translated and
                not (idx.part_reasons.get(h) and not idx.spans.get(h)))}
    table = name_table()
    keep = Keep()
    chosen = defaultdict(set)
    kept = []

    def take_class(h, d):
        if id(d) in keep.ids:
            return
        keep.ids.add(id(d))
        chosen[h].add(id(d))
        stack = [d]
        cname = d.name.split("<")[0].split("::")[-1] if d.name else ""
        while stack:
            c = stack.pop()
            for m in c.members:
                if m.kind in ("access", "macro-use", "other", "destructor"):
                    continue
                if m.kind == "constructor":
                    if cname in used:
                        keep.ids.add(id(m))
                        kept.append(m)
                    continue
                if m.name and m.name.split("<")[0] in used:
                    keep.ids.add(id(m))
                    kept.append(m)
                    if m.kind in ("class", "struct", "union"):
                        stack.append(m)
                    elif m.kind == "enum":
                        keep.enums[id(m)] = {v[0] for v in m.extra.get("values", []) if v[0] in used}

    def take(h, o, by_type=False):
        if isinstance(o, dict):
            keep.ids.add(o["name"])
            chosen[h].add(o["name"])
            return
        if o.kind in ("class", "struct", "union"):
            take_class(h, o)
            return
        if o.kind == "enum":
            names = {v[0] for v in o.extra.get("values", []) if v[0] in used}
            keep.enums[id(o)] = keep.enums.get(id(o), set()) | names
        keep.ids.add(id(o))
        chosen[h].add(id(o))
        kept.append(o)

    for nm in used:
        for h, o in table.get(nm, ()):
            if h in closure:
                take(h, o)
    done = set()
    for _ in range(2):
        named = set()
        for o in kept:
            if id(o) not in done and o.kind not in ("class", "struct", "union", "enum"):
                done.add(id(o))
                named |= set(IDENT_USE_RE.findall(o.text))
        added = False
        for nm in named - used:
            for h, o in table.get(nm, ()):
                if h in closure and isinstance(o, Decl) and o.kind in TYPE_KINDS and o.name and \
                        o.name.split("<")[0].split("::")[-1] == nm and id(o) not in keep.ids:
                    take(h, o, by_type=True)
                    added = True
        if not added:
            break
    return sorted(h for h in chosen if chosen[h]), keep


def split_file_macros(unit):
    idx = WORKER["index"]
    out = []
    for f in idx.units[unit]:
        if not f.endswith(IMPL_EXTS + (".ipp",)):
            continue
        spans = merged_spans(idx, f, 1 << 30)
        if len({m for _, _, m in spans}) < 2:
            continue
        text = idx.file_text(f)
        rows = []
        undefs = defaultdict(list)
        for m in re.finditer(r"^[ \t]*#[ \t]*undef[ \t]+(\w+)", text, re.M):
            undefs[m.group(1)].append(text.count("\n", 0, m.start()) + 1)
        for m in DEFINE_LINE_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            body = squash(m.group(3).replace("\\\n", " "))
            if len(body) > MACRO_TEXT_CAP:
                body = body[:MACRO_TEXT_CAP] + " ...(%d more chars)" % (len(body) - MACRO_TEXT_CAP)
            later = [u for u in undefs.get(m.group(1), ()) if u > line]
            rows.append("%d #define %s%s%s  // in %s%s" % (
                line, m.group(1), m.group(2) or "", (" " + body) if body else "", idx.module_at(f, line) or "-",
                ("; #undef at line %d" % later[0]) if later else ""))
        if rows:
            out.append("== %s  [this unit's own file, written as %d pieces; the macros it defines, so that a piece "
                       "sees the ones an earlier piece defines]" % (f, len({m for _, _, m in spans})))
            out.extend(rows)
            out.append("")
    return out


def unit_text(unit):
    idx = WORKER["index"]
    files = idx.units[unit]
    heads = idx.one_level(unit)
    WORKER["last_heads"] = sorted(heads)
    crate, mod = idx.unit_target(unit)
    reason = idx.not_translated.get(unit)
    mods = []
    for f in files:
        for m in idx.file_modules(f):
            if m not in mods:
                mods.append(m)
    if mods:
        target = ", ".join(mods)
    else:
        target = mod or (("not translated (%s)" % reason) if reason else ("no module (%s)" % crate if crate else
                                                                            "no crate-map match"))
    core_ids = sorted({uid for f in files for a, b, m, uid, t in idx.spans.get(f, ()) if uid.startswith("core/")})
    if core_ids:
        target += " (core module %s)" % ", ".join(core_ids)
    deeper, keep = select_referenced(unit, heads) if reason is None else ([], None)
    WORKER["last_heads"] = sorted(set(heads) | set(deeper))
    out = ["# decl-index %d for unit %s, written by migration/scripts/decl_index.py" % (FORMAT_VERSION, unit),
           "# unit files: " + " ".join(files),
           "# unit target: " + target,
           "# one-level headers: %d (files the unit's files #include directly, forwarders followed to their targets, "
           "a copy of another header replaced by the header it copies, and headers declaring what the unit defines), "
           "listed in full; deeper headers: %d (reached through further includes), listed with only the "
           "declarations the unit's files name and the types those declarations name; each read from its text, "
           "every #if branch kept but #if 0" % (len(heads), len(deeper)),
           "# layout: '== <file> [unit; crate; rust module]' starts a header, then '<line> <declaration>' for that file "
           "('<file>:<line>' when the declaration comes from another file, such as an X-macro list); members are "
           "indented under their class; '-- namespace X' gives the namespace of the lines below it",
           "# notes: '// rust: <name>' gives the Rust name where it differs (overloads numbered in header order per "
           "Rust scope, members a macro generates included: the first keeps the name, then _2, _3; an override keeps "
           "the name of the base method it overrides; constructors new, new_2; nested types Outer_Inner; members of "
           "a specialization as items of its trait impl; Rust keywords r#x); '// in <module>' gives the Rust module "
           "of a declaration when its header is written to several; '// from <MACRO>' marks a declaration generated "
           "by the macro use on that line; '// defined at <file>:<line>' names the file that defines a re-declared "
           "function and its crate; '// #if ...' gives the branch a declaration sits in; macros become lowercase "
           "macro_rules!",
           ""]
    for path in sorted(heads):
        out.extend(header_block(path, heads[path]))
        out.append("")
    out.extend(split_file_macros(unit))
    if deeper:
        out.append("# deeper headers: declarations the unit's files name, reached through further includes")
        out.append("")
        for path in deeper:
            out.extend(header_block(path, [], keep))
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def out_path(out_dir, unit):
    return os.path.join(out_dir, unit + ".txt")


def header_problems(path):
    res = parse_file(path)
    out = []
    if res["error"]:
        out.append(res["error"])
    for n in res["notes"]:
        if n.startswith("unbalanced") or "at file scope" in n or "is not indexed" in n:
            out.append(n)
    return out


def run_unit(unit):
    idx = WORKER["index"]
    dst = out_path(idx.out, unit)
    if os.path.exists(dst) and not idx.args.force:
        return unit, "skipped", os.path.getsize(dst), None, None, {}
    try:
        text = unit_text(unit)
        heads = WORKER.get("last_heads", [])
        problems = {h: header_problems(h) for h in heads}
        problems = {h: v for h, v in problems.items() if v}
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, dst)
    except Exception as e:
        return unit, "failed", 0, "%s: %s" % (type(e).__name__, e), None, {}
    return unit, "written", len(text.encode("utf-8")), None, len(heads), problems


def collect_macros(root, units):
    table = {}
    files = sorted({f for fs in units.values() for f in fs if f.endswith(HEADER_LIKE)})
    for f in files:
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in DEFINE_LINE_RE.finditer(text):
            name = m.group(1)
            if name in table:
                continue
            params = m.group(2)
            body = squash(m.group(3).replace("\\\n", " "))
            table[name] = ([p.strip() for p in params[1:-1].split(",")] if params else None, body)
    return table


def main():
    ap = argparse.ArgumentParser(description="Write migration/decl-index/<unit_id>.txt for every map unit.")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    ap.add_argument("--depmap", default="migration/depmap")
    ap.add_argument("--out", default="migration/decl-index")
    ap.add_argument("--build", default=None)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--units", nargs="*", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    t0 = time.time()
    index = Index(args)
    if os.path.isdir(index.out):
        for dirpath, _, names in os.walk(index.out):
            for nm in names:
                if ".txt.tmp." in nm:
                    os.remove(os.path.join(dirpath, nm))
    macros = collect_macros(index.root, index.units)
    GLOBAL_MACROS.update(macros)
    names = compute_names(index)
    bad_names = sorted({v for v in names.values() if not RUST_NAME_RE.match(v)})
    units = sorted(index.units) if not args.units else args.units
    for u in units:
        if u not in index.units:
            sys.exit("unknown unit: " + u)
    results = []
    jobs = max(1, min(args.jobs, 4))
    if jobs == 1:
        worker_init(args, macros, names)
        for u in units:
            results.append(run_unit(u))
    else:
        with Pool(jobs, initializer=worker_init, initargs=(args, macros, names)) as pool:
            for r in pool.imap(run_unit, units, chunksize=16):
                results.append(r)
    written = [r for r in results if r[1] == "written"]
    skipped = [r for r in results if r[1] == "skipped"]
    failed = [r for r in results if r[1] == "failed"]
    sizes = []
    for u in units:
        p = out_path(index.out, u)
        if os.path.exists(p):
            sizes.append(os.path.getsize(p))
    sizes.sort()
    problems = {}
    for r in written:
        for h, v in r[5].items():
            problems[h] = v
    no_headers = sorted(r[0] for r in written if r[4] == 0)

    def pct(q):
        return sizes[min(len(sizes) - 1, int(q * len(sizes)))] if sizes else 0

    summary = {"units": len(units), "written": len(written), "skipped": len(skipped), "failed": len(failed),
               "files_present": len(sizes), "bytes_present": sum(sizes), "seconds": round(time.time() - t0, 1),
               "bytes_median": pct(0.5), "bytes_p90": pct(0.9), "bytes_max": sizes[-1] if sizes else 0,
               "units_without_in_repo_headers": len(no_headers), "units_without_in_repo_headers_list": no_headers,
               "headers_with_problems": problems,
               "rust_names_not_identifiers": len(bad_names), "rust_names_not_identifiers_examples": bad_names[:20],
               "failures": [[r[0], r[3]] for r in failed]}
    text = json.dumps(summary, indent=1, sort_keys=True)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
