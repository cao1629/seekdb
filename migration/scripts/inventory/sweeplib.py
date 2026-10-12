"""Shared helpers for the phase-1c gap-inventory sweeps (stdlib only, deterministic).

Contract: migration/inventory/README.md (sweep file format, keys, counts.tsv).

- first-party file set  = paths in migration/depmap/nodes.tsv (the depmap already
  excludes vendored third-party, generated and unbuilt files; see excluded.tsv);
- comment/string stripping reuses `strip()` from
  migration/feasibility/scripts/arch/strip.py (line-preserving), loaded with `ast`
  so that its module-level `main()` is not executed;
- a light C++ scope parser (namespaces, classes, functions, blocks) on the stripped
  text, used to name the enclosing function or class of a site;
- write_sweep() checks the (file, symbol, source_construct) key for uniqueness and
  writes rows sorted by (file, line, symbol, source_construct);
- update_counts() rewrites only the given categories' rows of counts.tsv and keeps
  every other row (another agent writes there too), sorted by category.

Run every script from anywhere with `python3 -I`; paths are resolved from this file.
"""
import ast
import bisect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SWEEP_DIR = os.path.join(REPO, 'migration', 'inventory', 'sweep')
STRIP_PY = os.path.join(REPO, 'migration', 'feasibility', 'scripts', 'arch', 'strip.py')
NODES_TSV = os.path.join(REPO, 'migration', 'depmap', 'nodes.tsv')
CODE_EXT = ('.h', '.hpp', '.ipp', '.inc', '.cpp', '.cc', '.c', '.def', '.map')
SOURCE_EXT = ('.h', '.hpp', '.ipp', '.inc', '.cpp', '.cc', '.c')
PARSER_DIRS = ('src/sql/parser/', 'src/pl/parser/', 'src/sql/pl/parser/', 'src/query/api/query/parser/')


def _load_strip():
    with open(STRIP_PY, encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), STRIP_PY)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'strip']
    if len(fns) != 1:
        sys.exit('sweeplib: cannot find strip() in %s' % STRIP_PY)
    ns = {}
    exec(compile(ast.Module(body=fns, type_ignores=[]), STRIP_PY, 'exec'), ns)
    return ns['strip']


def strip_line_preserving(src):
    """feasibility strip() with newlines kept inside string/char literals and after unterminated quotes.

    The feasibility strip() (still loadable via _load_strip()) drops the newline of a backslash-newline
    inside a string or character literal and the newline that ends an unterminated quote, which shifts
    every later line number in 16 first-party files.  Everything else is the same algorithm."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ''
        if c == '/' and nx == '/':
            j = src.find('\n', i)
            if j < 0:
                j = n
            i = j
            continue
        if c == '/' and nx == '*':
            j = src.find('*/', i + 2)
            if j < 0:
                j = n - 2
            out.append('\n' * src.count('\n', i, j + 2))
            i = j + 2
            continue
        if c == 'R' and nx == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_')) or \
           (c in 'uUL8' and src.startswith('R"', i + 1) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == '_'))):
            k = src.find('"', i)
            p = src.find('(', k)
            delim = src[k + 1:p]
            if p > 0 and len(delim) <= 16 and '\n' not in delim:
                end = src.find(')' + delim + '"', p)
                if end > 0:
                    out.append('""' + '\n' * src.count('\n', i, end))
                    i = end + len(delim) + 2
                    continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != '\n':
                if src[j] == '\\':
                    j += 1
                j += 1
            out.append('""' + '\n' * src.count('\n', i, min(j + 1, n)))
            i = j + 1
            continue
        if c == "'":
            prev = src[i - 1] if i > 0 else ''
            if prev.isdigit() and nx.isalnum():  # digit separator
                out.append(c)
                i += 1
                continue
            j = i + 1
            while j < n and src[j] != "'" and src[j] != '\n':
                if src[j] == '\\':
                    j += 1
                j += 1
            out.append("' '" + '\n' * src.count('\n', i, min(j + 1, n)))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)



strip_text = strip_line_preserving

_FIRST_PARTY = None


def first_party(exts=SOURCE_EXT):
    """Repo-relative first-party paths (depmap nodes), sorted, filtered by extension."""
    global _FIRST_PARTY
    if _FIRST_PARTY is None:
        with open(NODES_TSV, encoding='utf-8') as fh:
            next(fh)
            _FIRST_PARTY = sorted(line.split('\t', 1)[0] for line in fh if line.strip())
    return [p for p in _FIRST_PARTY if p.endswith(exts)]


_RAW, _STRIPPED, _STRUCT, _SCOPES, _LINESTART = {}, {}, {}, {}, {}


def raw(path):
    if path not in _RAW:
        with open(os.path.join(REPO, path), encoding='utf-8', errors='replace') as fh:
            _RAW[path] = fh.read()
    return _RAW[path]


def raw_lines(path):
    return raw(path).split('\n')


def stripped(path):
    """Comment- and string-literal-stripped text, same line numbering as the original."""
    if path not in _STRIPPED:
        _STRIPPED[path] = strip_text(raw(path))
    return _STRIPPED[path]


def stripped_lines(path):
    return stripped(path).split('\n')


def line_of(path, offset):
    """1-based line number of an offset into stripped(path) (same lines as the original)."""
    starts = _LINESTART.get(path)
    if starts is None:
        text = stripped(path)
        starts = [0] + [m.end() for m in re.finditer(r'\n', text)]
        _LINESTART[path] = starts
    return bisect.bisect_right(starts, offset)


def line_start_offset(path, line):
    line_of(path, 0)
    return _LINESTART[path][line - 1]


# ---------------------------------------------------------------- preprocessor
_PP_LINE = re.compile(r'^[ \t]*#')
_PP_IF = re.compile(r'^[ \t]*#[ \t]*(if|ifdef|ifndef)\b(.*)$')
_PP_ELSE = re.compile(r'^[ \t]*#[ \t]*(elif|else)\b')
_PP_ENDIF = re.compile(r'^[ \t]*#[ \t]*endif\b')


def structural_text(path):
    """Stripped text with directive lines blanked and, for brace matching, only one branch
    of each conditional kept: the first branch, except `#if 0` keeps its #else part."""
    if path in _STRUCT:
        return _STRUCT[path]
    lines = stripped(path).split('\n')
    out = []
    stack = []  # per #if: [keep_current_branch, branch_index, is_if0]
    cont = False
    for ln in lines:
        is_dir = cont or bool(_PP_LINE.match(ln))
        if is_dir and not cont:
            m = _PP_IF.match(ln)
            if m:
                is_if0 = m.group(1) == 'if' and m.group(2).strip() in ('0', '(0)')
                stack.append([not is_if0, 0, is_if0])
            elif _PP_ELSE.match(ln) and stack:
                top = stack[-1]
                top[1] += 1
                top[0] = top[2] and top[1] == 1
            elif _PP_ENDIF.match(ln) and stack:
                stack.pop()
        cont = is_dir and ln.rstrip().endswith('\\')
        active = all(s[0] for s in stack)
        if is_dir or not active:
            out.append(' ' * len(ln))
        else:
            out.append(ln)
    _STRUCT[path] = '\n'.join(out)
    return _STRUCT[path]


# ---------------------------------------------------------------- scope parser
class Scope(object):
    __slots__ = ('kind', 'name', 'open', 'close', 'header')

    def __init__(self, kind, name, open_, header):
        self.kind, self.name, self.open, self.close, self.header = kind, name, open_, None, header

    def __repr__(self):
        return 'Scope(%s %r %s-%s)' % (self.kind, self.name, self.open, self.close)


_KW_BLOCK = {'if', 'for', 'while', 'switch', 'catch', 'return', 'sizeof', 'decltype', 'alignof', 'noexcept',
             'throw', '__attribute__', 'alignas', 'static_assert', 'STATIC_ASSERT', '__declspec', 'defined'}
_RE_TEMPLATE = re.compile(r'\btemplate\s*<')
_RE_NS = re.compile(r'^(?:inline\s+)?namespace\b\s*([\w:]*)\s*$')
_RE_LINKAGE = re.compile(r'^extern\s*""\s*$')
_RE_ENUM = re.compile(r'(?:^|[\s;])enum\b(?:\s+(?:class|struct))?\s*(\w*)\s*(?::[^(){}]*)?$')
_RE_CLASS = re.compile(r'(?:^|[\s;>])(class|struct|union)\s+((?:(?:__attribute__\s*\(\(.*?\)\)|alignas\s*\(.*?\)|'
                       r'[A-Z][A-Z0-9_]+(?:\s*\([^()]*\))?)\s+)*)([A-Za-z_]\w*)?\s*(?:final\s*)?(:[^(){}]*)?$')
_RE_ANON_CLASS = re.compile(r'(?:^|[\s;>{])(class|struct|union)\s*$')


def _strip_template_prefix(h):
    while True:
        m = _RE_TEMPLATE.search(h)
        if not m:
            return h
        i, depth = m.end(), 1
        while i < len(h) and depth:
            if h[i] == '<':
                depth += 1
            elif h[i] == '>':
                depth -= 1
            i += 1
        h = h[:m.start()] + ' ' + h[i:]


def _top_level_parens(h):
    """(open, close) index pairs of top-level parenthesised groups in h (braces skipped)."""
    groups, depth, start, bdepth = [], 0, -1, 0
    for i, c in enumerate(h):
        if c == '{':
            bdepth += 1
        elif c == '}':
            bdepth = max(0, bdepth - 1)
        elif bdepth:
            continue
        elif c == '(':
            if depth == 0:
                start = i
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0 and start >= 0:
                groups.append((start, i))
            depth = max(depth, 0)
    return groups


_RE_OPERATOR = re.compile(r'\boperator\s*(\(\s*\)|\[\s*\]|new\s*\[\s*\]|delete\s*\[\s*\]|new\b|delete\b|'
                          r'->\*?|<<=|>>=|<=>|[-+*/%^&|~!=<>]=?|<<|>>|&&|\|\||\+\+|--|,|\s+[\w:<>\s*&]+?)\s*$')


def _name_before(h, p):
    """Qualified name ending right before index p in h (template args removed) or None."""
    left = h[:p].rstrip()
    m = _RE_OPERATOR.search(left)
    if m:
        name_end = m.start()
        op = 'operator' + re.sub(r'\s+', ' ', m.group(1)).strip() if not m.group(1).startswith(' ') \
            else 'operator ' + re.sub(r'\s+', ' ', m.group(1)).strip()
        qual = _qualifier_before(left[:name_end])
        return (qual + '::' + op) if qual else op
    i = len(left)
    parts = []
    while True:
        j = i
        while j > 0 and (left[j - 1].isalnum() or left[j - 1] == '_'):
            j -= 1
        if j == i:
            break
        ident = left[j:i]
        if j > 0 and left[j - 1] == '~':
            j -= 1
            ident = '~' + ident
        parts.append(ident)
        k = j
        while k > 0 and left[k - 1] in ' \t\n':
            k -= 1
        if k >= 2 and left[k - 2:k] == '::':
            k -= 2
            while k > 0 and left[k - 1] in ' \t\n':
                k -= 1
            if k > 0 and left[k - 1] == '>':
                depth, k = 1, k - 1
                while k > 0 and depth:
                    k -= 1
                    if left[k] == '>':
                        depth += 1
                    elif left[k] == '<':
                        depth -= 1
                while k > 0 and left[k - 1] in ' \t\n':
                    k -= 1
            i = k
            continue
        break
    if not parts:
        return None
    return '::'.join(reversed(parts))


def _qualifier_before(left):
    left = left.rstrip()
    if not left.endswith('::'):
        return ''
    q = _name_before(left[:-2] + '(', len(left) - 2)
    return q or ''


def _classify(header, parent_kind):
    """Kind and name of the scope opened by `{` after `header` (text since the last boundary)."""
    if parent_kind in ('function', 'block', 'lambda'):
        return 'block', ''
    if parent_kind in ('init', 'enum'):
        return 'init', ''
    h = ' '.join(header.split())
    h = _strip_template_prefix(h).strip()
    h = re.sub(r'^(?:public|private|protected)\s*:\s*', '', h)
    if not h:
        return 'init', ''
    m = _RE_NS.match(h)
    if m:
        return 'namespace', m.group(1)
    if _RE_LINKAGE.match(h):
        return 'linkage', ''
    if h.endswith('=') or h.endswith(',') or h.endswith('(') or h.endswith('return'):
        return 'init', ''
    groups = _top_level_parens(h)
    m = _RE_ENUM.search(h)
    if m and not groups:
        return 'enum', m.group(1)
    if not groups or (groups and _RE_CLASS.search(h) and _RE_CLASS.search(h).start() > groups[-1][1]):
        m = _RE_CLASS.search(h)
        if m:
            return 'class', m.group(3) or ''
        if _RE_ANON_CLASS.search(h):
            return 'class', ''
        if not groups:
            if h.endswith(']'):
                return 'lambda', ''
            return 'init', ''
    for (o, c) in groups:
        before = h[:o].rstrip()
        if not before:
            continue
        if before.endswith(']'):
            return 'lambda', ''
        name = _name_before(h, o)
        if not name:
            continue
        base = name.split('::')[-1]
        if base in ('if', 'for', 'while', 'switch', 'catch'):
            return 'block', ''
        if base in _KW_BLOCK:
            continue
        rest = _strip_braced(h[c + 1:]).strip()
        if not _valid_suffix(rest):
            continue
        if _has_init_list(rest) and re.search(r'[\w>]$', h[c + 1:].rstrip()):
            return 'init', ''  # brace initialiser inside a constructor initialiser list
        if re.match(r'^[A-Z][A-Z0-9_]*$', base) and not before[:-len(base)].strip():
            args = re.sub(r'\s+', '', h[o + 1:c])[:60]
            return 'function', '%s(%s)' % (name, args)
        return 'function', name
    return 'init', ''


_SUFFIX_TOKEN = re.compile(r'(?:const|volatile|override|final|noexcept(?:\s*\([^()]*\))?|throw\s*\(\s*\)|mutable|'
                           r'&&|&|[A-Z][A-Z0-9_]+(?:\s*\([^()]*\))?|__attribute__\s*\(\(.*?\)\)|'
                           r'->\s*[\w:<>,*& ]+)')


def _valid_suffix(rest):
    """True if `rest` (text after a parameter list) can follow a function declarator before its body."""
    i, n = 0, len(rest)
    while i < n:
        if rest[i].isspace():
            i += 1
            continue
        if rest[i] == ':' and rest[i:i + 2] != '::':
            return True  # constructor initialiser list
        m = _SUFFIX_TOKEN.match(rest, i)
        if not m or m.end() == i:
            return False
        i = m.end()
    return True


def _has_init_list(rest):
    return bool(re.search(r'(?<!:):(?!:)', rest))


def _strip_braced(s):
    out, depth = [], 0
    for c in s:
        if c == '{':
            depth += 1
        elif c == '}':
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(c)
    return ''.join(out)


def scopes(path):
    """All brace scopes of a file (sorted by open offset) with kind/name; offsets into stripped(path)."""
    if path in _SCOPES:
        return _SCOPES[path]
    text = structural_text(path)
    result, stack = [], []
    header_start = 0
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == '{':
            parent = stack[-1].kind if stack else 'namespace'
            kind, name = _classify(text[header_start:i], parent)
            s = Scope(kind, name, i, text[header_start:i])
            stack.append(s)
            result.append(s)
            if kind not in ('init',):
                header_start = i + 1
        elif c == '}':
            if stack:
                s = stack.pop()
                s.close = i
                if s.kind == 'class' and not s.name:
                    m = re.match(r'\s*(\w+)\s*[;,\[]', text[i + 1:i + 200])
                    if m and m.group(1) not in ('const', 'volatile'):
                        s.name = m.group(1)
                if s.kind != 'init':
                    header_start = i + 1
            else:
                header_start = i + 1
        elif c == ';':
            parent = stack[-1].kind if stack else 'namespace'
            if parent in ('namespace', 'linkage', 'class'):
                header_start = i + 1
        elif c == ':' and stack and stack[-1].kind == 'class':
            # access specifier ends a header inside a class body
            j = i - 1
            while j >= 0 and text[j] in ' \t\n':
                j -= 1
            k = j
            while k >= 0 and (text[k].isalnum() or text[k] == '_'):
                k -= 1
            if text[k + 1:j + 1] in ('public', 'private', 'protected') and text[i + 1:i + 2] != ':' and text[i - 1:i] != ':':
                header_start = i + 1
        i += 1
    for s in stack:
        s.close = n
    result.sort(key=lambda s: s.open)
    _SCOPES[path] = result
    return result


def scope_chain(path, offset):
    """Scopes containing offset, outermost first."""
    return [s for s in scopes(path) if s.open < offset <= (s.close if s.close is not None else 1 << 60)]


def _header_function(path, offset):
    """Function scope whose declarator/initialiser-list header contains offset (offset before its `{`)."""
    for s in scopes(path):
        if s.kind == 'function' and s.open - len(s.header) <= offset < s.open:
            return s
    return None


def enclosing_function(path, offset):
    """Qualified name of the innermost function whose body (or declarator/initialiser list) contains offset."""
    chain = scope_chain(path, offset)
    fn = None
    classes = []
    for s in chain:
        if s.kind == 'class':
            if fn is None and s.name:
                classes.append(s.name)
        elif s.kind == 'function':
            if fn is None:
                fn = s
                fn_classes = list(classes)
    if fn is None:
        st, sx = structural_text(path), stripped(path)
        if offset < len(sx) and not sx[offset].isspace() and st[offset].isspace():
            return None  # inside a directive (e.g. a #define body) or an unselected #if branch
        hf = _header_function(path, offset)
        if hf is None:
            return None
        fn = hf
        fn_classes = [s.name for s in scope_chain(path, hf.open) if s.kind == 'class' and s.name]
    name = fn.name
    if fn_classes and '::' not in name.split('(')[0]:
        name = '::'.join(fn_classes) + '::' + name
    elif fn_classes and not name.startswith(fn_classes[-1] + '::'):
        name = '::'.join(fn_classes) + '::' + name
    return name


def enclosing_class(path, offset):
    """'Outer::Inner' if offset is directly in a class body (not inside a function), else None."""
    chain = scope_chain(path, offset)
    names = []
    for s in chain:
        if s.kind in ('function', 'block', 'lambda'):
            return None
        if s.kind == 'class':
            if s.name:
                names.append(s.name)
    if not chain or chain[-1].kind != 'class':
        return None
    return '::'.join(names) if names else '<anonymous>'


_RX_DEFINE = re.compile(r'^[ \t]*#[ \t]*define[ \t]+(\w+)')


def macro_at(path, offset):
    """Name of the #define whose (possibly continued) body contains offset, else None."""
    lines = stripped_lines(path)
    ln = line_of(path, offset) - 1
    i = ln
    while i >= 0:
        m = _RX_DEFINE.match(lines[i])
        if m:
            # every line from i to ln-1 must end with a continuation backslash
            if all(lines[k].rstrip().endswith('\\') for k in range(i, ln)):
                return m.group(1)
            return None
        if i < ln and not lines[i].rstrip().endswith('\\'):
            return None
        i -= 1
    return None


def site_owner(path, offset):
    """Enclosing function name, else `macro:<NAME>` for a site inside a #define body, else `<file-scope>`."""
    mac = macro_at(path, offset)
    if mac:
        return 'macro:' + mac
    fn = enclosing_function(path, offset)
    if fn:
        return fn
    return '<file-scope>'


def in_function(path, offset):
    return any(s.kind in ('function', 'block', 'lambda') for s in scope_chain(path, offset))


def function_scope(path, offset):
    """The innermost 'function' Scope containing offset (or None)."""
    fn = None
    for s in scope_chain(path, offset):
        if s.kind == 'function':
            fn = s
    return fn


# ---------------------------------------------------------------- output
HEADER = 'file\tsymbol\tsource_construct\tline\tpretag\n'


def _clean(v):
    v = '' if v is None else str(v)
    return ' '.join(v.replace('\t', ' ').split())


def write_sweep(category, rows, out_dir=SWEEP_DIR):
    """rows: iterable of (file, symbol, source_construct, line, pretag). Exits non-zero on a duplicate key."""
    seen = {}
    clean = []
    for r in rows:
        f, sym, cons, line, pre = (_clean(x) for x in r)
        key = (f, sym, cons)
        if key in seen:
            sys.exit('%s: duplicate key %r (lines %s and %s)' % (category, key, seen[key], line))
        seen[key] = line
        clean.append((f, sym, cons, int(line), pre))
    clean.sort(key=lambda r: (r[0], r[3], r[1], r[2]))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, category + '.tsv')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(HEADER)
        for f, sym, cons, line, pre in clean:
            fh.write('%s\t%s\t%s\t%d\t%s\n' % (f, sym, cons, line, pre))
    return path, len(clean)


def write_tsv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\t'.join(header) + '\n')
        for r in rows:
            fh.write('\t'.join(_clean(x) for x in r) + '\n')


def update_counts(updates, out_dir=SWEEP_DIR):
    """updates: {category: (sites, note)}. Rewrites only those rows of counts.tsv, keeps the rest."""
    path = os.path.join(out_dir, 'counts.tsv')
    rows = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        for ln in lines[1:]:
            if not ln.strip():
                continue
            parts = ln.split('\t')
            while len(parts) < 3:
                parts.append('')
            rows[parts[0]] = (parts[1], parts[2])
    for cat, (n, note) in updates.items():
        rows[cat] = (str(n), _clean(note))
    tmp = path + '.tmp.%d' % os.getpid()
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('category\tsites\tnote\n')
        for cat in sorted(rows):
            fh.write('%s\t%s\t%s\n' % (cat, rows[cat][0], rows[cat][1]))
    os.replace(tmp, path)


def finish(category, rows, note):
    path, n = write_sweep(category, rows)
    update_counts({category: (n, note)})
    print('%s: %d rows -> %s' % (category, n, os.path.relpath(path, REPO)))
    return n


def rel(p):
    return os.path.relpath(p, REPO).replace(os.sep, '/')


def in_parser_dir(path):
    return path.startswith(PARSER_DIRS)


def grep_stripped(paths, regex, raw_prefilter=None):
    """Yield (path, match) for regex over the stripped text of each path.
    raw_prefilter: substring that must occur in the raw text (speed only)."""
    rx = re.compile(regex) if isinstance(regex, str) else regex
    for p in paths:
        if raw_prefilter is not None and raw_prefilter not in raw(p):
            continue
        for m in rx.finditer(stripped(p)):
            yield p, m
