"""Shared helpers of sweep set S3b (c06 c07 c08 c09 c15 and the name tables); stdlib only, deterministic.

Builds on sweeplib.py (first-party set, comment/string stripping, scope parser, sweep writer) and
astdb.py (libclang declaration database).  Two differences from plain sweeplib use:

- a line-preserving stripper is installed into sweeplib (`sweeplib.strip_text`) for the S3b scripts:
  feasibility `strip()` drops the newline of a backslash-newline inside a string or character literal
  and the newline that ends an unterminated quote, so 16 first-party files lose 1-119 lines
  (`ob_dbms_stats_history_manager.cpp` 1433 -> 1314) and every later line number shifts.  The fixed
  stripper emits those newlines; everything else is byte-for-byte the same algorithm;
- function bodies are located from the database (`line`/`end_line` of the definition record), not by
  parsing the whole file.
"""
import bisect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sweeplib as L  # noqa: E402
import astdb  # noqa: E402

REPO = L.REPO
DESIGN = os.path.join(REPO, 'migration', 'design')
UNITS_TSV = os.path.join(DESIGN, 'units.tsv')
FILES_TSV = os.path.join(REPO, 'build_release', 'inventory-ast', 'files.tsv')
ERRNO_DEF = os.path.join(REPO, 'src', 'share', 'ob_errno.def')
PARSE_DEFINE = os.path.join(REPO, 'src', 'sql', 'parser', 'parse_define.h')


# ---------------------------------------------------------------- line-preserving stripper
strip_fixed = L.strip_line_preserving  # now the sweeplib default; kept as an alias

L.strip_text = strip_fixed


# ---------------------------------------------------------------- database, files, units
def load_db(kinds=None):
    return astdb.AstDB(kinds=kinds)


def first_party_code():
    return L.first_party(L.SOURCE_EXT)


def tus0_files():
    """First-party source files no translation unit of the baseline build includes (files.tsv tus=0)."""
    out = []
    fp = set(first_party_code())
    with open(FILES_TSV, encoding='utf-8') as fh:
        head = fh.readline().rstrip('\n').split('\t')
        i_f, i_t = head.index('file'), head.index('tus')
        for ln in fh:
            r = ln.rstrip('\n').split('\t')
            if r[i_t] == '0' and r[i_f] in fp:
                out.append(r[i_f])
    return sorted(out)


_UNITS = None


class Unit(object):
    __slots__ = ('unit', 'members', 'target', 'crate', 'disposition', 'order', 'stem', 'module_path')

    def __init__(self, row, head, order):
        self.unit = row[head.index('unit')]
        self.members = row[head.index('members')].split(',')
        self.target = row[head.index('target')]
        self.crate = row[head.index('crate')]
        self.disposition = row[head.index('disposition')]
        self.order = order
        base = os.path.basename(self.target)
        self.stem = base[:-3] if base.endswith('.rs') else base
        # rust/port/<crate>/src/<path>.rs -> <path> with '/' -> '::'
        m = re.match(r'rust/port/[^/]+/src/(.*)\.rs$', self.target)
        self.module_path = m.group(1).replace('/', '::') if m else self.stem


def units():
    """file -> Unit (every member file of every units.tsv row); rows keep the units.tsv order (dependency order)."""
    global _UNITS
    if _UNITS is None:
        by_file = {}
        with open(UNITS_TSV, encoding='utf-8') as fh:
            head = fh.readline().rstrip('\n').split('\t')
            for k, ln in enumerate(fh):
                r = ln.rstrip('\n').split('\t')
                u = Unit(r, head, k)
                for f in u.members:
                    by_file[f] = u
        _UNITS = by_file
    return _UNITS


def unit_of(path):
    return units().get(path)


# ---------------------------------------------------------------- error codes
_ERRNO = None


def errno_names():
    """Error-code constant names: every DEFINE_*(OB_X, ...) of ob_errno.def, the parser's OB_PARSER_* codes
    (parse_define.h) and the serialization macro OK_ (= OB_SUCCESS, ob_unify_serialize.h:50)."""
    global _ERRNO
    if _ERRNO is None:
        with open(ERRNO_DEF, encoding='utf-8') as fh:
            names = set(re.findall(r'^\s*DEFINE_\w+\s*\(\s*(OB_\w+)', fh.read(), re.M))
        with open(PARSE_DEFINE, encoding='utf-8') as fh:
            names |= set(re.findall(r'\b(OB_PARSER_\w+)\s*=', fh.read()))
        names.add('OK_')
        _ERRNO = names
    return _ERRNO


# ---------------------------------------------------------------- bodies
def body_span(rec):
    """(open, close) offsets into L.stripped(file) of the brace block that ends on rec's end_line, i.e. the
    function body of a definition record; None when the extent holds no brace block (body inside a macro)."""
    path = rec['file']
    if rec.get('end_line') is None:
        return None
    st = L.structural_text(path)
    lines_n = st.count('\n') + 1
    if rec['line'] > lines_n or rec['end_line'] > lines_n:
        return None
    a = L.line_start_offset(path, rec['line'])
    e = L.line_start_offset(path, rec['end_line'])
    b = st.find('\n', e)
    if b < 0:
        b = len(st)
    for text in (st, L.stripped(path)):
        seg = text[a:b]
        ci = seg.rfind('}')
        if ci < 0:
            continue
        depth, i = 0, ci
        while i >= 0:
            ch = seg[i]
            if ch == '}':
                depth += 1
            elif ch == '{':
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        if i >= 0:
            return (a + i, a + ci)
    return None


_RX_LAMBDA_HEAD = re.compile(r'\]\s*(?:\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?\s*(?:mutable\b\s*)?'
                             r'(?:constexpr\b\s*)?(?:noexcept\b\s*)?(?:->\s*[^{;]+?)?\s*$')
_RX_LOCAL_CLASS_HEAD = re.compile(r'\b(?:struct|class|union)\b[^;(){}]*$')


_RX_BRACE_SEMI = re.compile(r'[{};]')


def inner_ranges(path, ob, cb):
    """Brace ranges [(open, close, kind)] of lambdas ('lambda') and local classes ('class') strictly inside
    the body (ob, cb) of a function (offsets into L.stripped(path))."""
    text = L.structural_text(path)
    out = []
    stack = []
    last = ob + 1
    for m in _RX_BRACE_SEMI.finditer(text, ob + 1, cb):
        i = m.start()
        ch = text[i]
        if ch == '{':
            head = text[last:i]
            kind = 'block'
            if _RX_LAMBDA_HEAD.search(head[-400:]):
                kind = 'lambda'
            elif _RX_LOCAL_CLASS_HEAD.search(head[-400:]):
                kind = 'class'
            stack.append((i, kind))
            last = i + 1
        elif ch == '}':
            if stack:
                o, kind = stack.pop()
                if kind != 'block':
                    out.append((o, i, kind))
            last = i + 1
        else:
            last = i + 1
    out.sort()
    return out


def blank_ranges(text, base, ranges):
    """text (starting at offset base) with the given (open, close, kind) ranges replaced by spaces, newlines kept."""
    if not ranges:
        return text
    chars = list(text)
    for o, c, _k in ranges:
        for k in range(max(o - base, 0), min(c - base + 1, len(chars))):
            if chars[k] != '\n':
                chars[k] = ' '
    return ''.join(chars)


def in_ranges(off, ranges):
    for o, c, _k in ranges:
        if o < off < c:
            return True
    return False


def inner_ranges_text(text):
    """inner_ranges() on an arbitrary text block (offsets relative to text; text[0] is the body's `{`)."""
    out = []
    stack = []
    last = 1
    for m in _RX_BRACE_SEMI.finditer(text, 1, max(len(text) - 1, 1)):
        i = m.start()
        ch = text[i]
        if ch == '{':
            head = text[last:i]
            kind = 'block'
            if _RX_LAMBDA_HEAD.search(head[-400:]):
                kind = 'lambda'
            elif _RX_LOCAL_CLASS_HEAD.search(head[-400:]):
                kind = 'class'
            stack.append((i, kind))
            last = i + 1
        elif ch == '}':
            if stack:
                o, kind = stack.pop()
                if kind != 'block':
                    out.append((o, i, kind))
            last = i + 1
        else:
            last = i + 1
    out.sort()
    return out


def line_of(path, off):
    return L.line_of(path, off)


# ---------------------------------------------------------------- types of names inside function bodies
RX_LOCAL_DECL = (r'(?:^|[;{}(,]\s*)((?:const\s+|volatile\s+|static\s+)*[A-Za-z_][\w:]*(?:\s*<[^;(){}]*?>)?'
                 r'(?:\s*::\s*\w+)*\s*[*&]*)\s*\b%s\s*(?:[;=,)]|\(|\{|\[)')
_NOT_TYPES = {'return', 'else', 'case', 'new', 'delete', 'goto', 'throw', 'typedef', 'using', 'sizeof'}


class TypeEnv(object):
    """Resolves the declared type of a name used in a function body (parameter, field of the enclosing class
    including inherited ones, or local declaration found by regex) and the record a type names."""

    def __init__(self, db):
        self.db = db
        self.records_by_qname = {}
        self.records_by_name = {}
        for r in db.records:
            if r['kind'] in astdb.RECORD_KINDS and r.get('is_definition'):
                self.records_by_qname.setdefault(r['qname'], r['usr'])
                self.records_by_name.setdefault(r['name'], [])
                if r['usr'] not in self.records_by_name[r['name']]:
                    self.records_by_name[r['name']].append(r['usr'])
        self._fields = {}

    def fields(self, rec_usr):
        """name -> field record (inherited fields included; the nearest declaration wins)."""
        if rec_usr not in self._fields:
            fs = {}
            try:
                lst = self.db.fields(rec_usr, inherited=True)
            except ValueError:
                lst = []
            for f in reversed(lst):           # derived fields come last in layout order: they shadow bases
                fs[f['name']] = f
            self._fields[rec_usr] = fs
        return self._fields[rec_usr]

    def class_of(self, defrec):
        pu = defrec.get('parent_usr')
        if pu and self.db.definition(pu, astdb.RECORD_KINDS) is not None:
            return pu
        return None

    def var_type(self, defrec, body_text, var):
        """(type string, origin) of `var` in the function of definition record defrec; origin is
        param / field / local; (None, None) if unknown."""
        for p in defrec.get('params', []) or []:
            if p.get('name') == var:
                return (p.get('canonical_type') or p.get('type') or '', 'param')
        cu = self.class_of(defrec)
        if cu:
            f = self.fields(cu).get(var)
            if f is not None:
                return (f.get('canonical_type') or f.get('type') or '', 'field')
        m = re.search(RX_LOCAL_DECL % re.escape(var), body_text or '')
        if m:
            t = m.group(1).strip()
            if t.split()[-1].rstrip('*&') not in _NOT_TYPES and t not in ('auto', 'auto *', 'auto &', 'const auto &'):
                return (t, 'local')
        return (None, None)

    def record_of_type(self, t, arrow=None):
        """Record usr named by type string t (pointer/reference/const stripped; template arguments dropped).
        arrow=True requires a pointer, arrow=False a non-pointer, None accepts both."""
        if not t:
            return None
        t = re.sub(r'\b(?:const|volatile|struct|class|union|typename|static)\b', '', t).strip()
        is_ptr = bool(re.search(r'\*\s*$', t)) and t.count('*') == 1
        base = re.sub(r'[*&\s]+$', '', t).strip()
        if arrow is True and not is_ptr:
            return None
        if arrow is False and is_ptr:
            return None
        base = re.sub(r'<.*>$', '', base).strip()
        if not base:
            return None
        if base in self.records_by_qname:
            return self.records_by_qname[base]
        if ('oceanbase::' + base) in self.records_by_qname:
            return self.records_by_qname['oceanbase::' + base]
        short = base.split('::')[-1]
        us = self.records_by_name.get(short, [])
        cands = [u for u in us if self.db.definition(u, astdb.RECORD_KINDS)['qname'].endswith('::' + base) or
                 self.db.definition(u, astdb.RECORD_KINDS)['qname'] == base]
        if len(cands) == 1:
            return cands[0]
        return us[0] if len(us) == 1 else None


# ---------------------------------------------------------------- definitions with bodies
def definitions_with_bodies(db, kinds=astdb.FUNC_KINDS):
    """[(defrec, (ob, cb))] for every non-generated function definition whose body is in its source file."""
    out = []
    for r in db.records:
        if r['kind'] in kinds and r.get('is_definition') and not r.get('generated') and r.get('end_line'):
            sp = body_span(r)
            if sp is not None:
                out.append((r, sp))
    out.sort(key=lambda x: (x[0]['file'], x[0]['line'], x[0].get('col', 0), x[0]['usr']))
    return out


def brace_blocks(text):
    """[(open, close)] of all balanced brace pairs in text (relative offsets), sorted by open."""
    out, stack = [], []
    for m in _RX_BRACE_SEMI.finditer(text):
        ch = text[m.start()]
        if ch == '{':
            stack.append(m.start())
        elif ch == '}' and stack:
            out.append((stack.pop(), m.start()))
    out.sort()
    return out


def innermost_block(blocks, pos):
    """The innermost (open, close) of blocks containing pos, or None."""
    best = None
    for o, c in blocks:
        if o > pos:
            break
        if o < pos < c and (best is None or o >= best[0]):
            best = (o, c)
    return best


# ---------------------------------------------------------------- #define index
class MacroDef(object):
    """One `#define NAME[(params)] text` (comment/string-stripped; continuation lines joined with '\\n')."""
    __slots__ = ('name', 'file', 'line', 'params', 'text', 'body_off', 'nlines')

    def __init__(self, name, path, line, params, text, body_off, nlines):
        self.name, self.file, self.line, self.params = name, path, line, params
        self.text, self.body_off, self.nlines = text, body_off, nlines

    @property
    def function_like(self):
        return self.params is not None

    def line_at(self, off):
        """Source line of an offset into self.text."""
        return self.line + self.text.count('\n', 0, off)


_RX_DEFINE = re.compile(r'^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(\()?')
_MACROS = None


def macro_index():
    """name -> [MacroDef] over every first-party file (all branches of #if; .cpp-local macros included)."""
    global _MACROS
    if _MACROS is not None:
        return _MACROS
    idx = {}
    for p in L.first_party(L.CODE_EXT):
        text = L.stripped(p)
        if '#' not in text:
            continue
        lines = text.split('\n')
        i = 0
        n = len(lines)
        while i < n:
            m = _RX_DEFINE.match(lines[i])
            if not m:
                i += 1
                continue
            j = i
            while lines[j].rstrip().endswith('\\') and j + 1 < n:
                j += 1
            block = '\n'.join(ln.rstrip()[:-1] if ln.rstrip().endswith('\\') else ln for ln in lines[i:j + 1])
            params = None
            body_off = m.end()
            if m.group(2):
                close = block.find(')', m.end())
                if close >= 0:
                    params = [x.strip() for x in block[m.end():close].split(',') if x.strip()]
                    body_off = close + 1
            idx.setdefault(m.group(1), []).append(MacroDef(m.group(1), p, i + 1, params, block, body_off, j - i + 1))
            i = j + 1
    _MACROS = idx
    return idx


def snake(name):
    """CamelCase -> snake_case (ObColumnRefRawExpr -> ob_column_ref_raw_expr); keeps existing underscores."""
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    s = re.sub(r'__+', '_', s)
    return s.lower().strip('_')


def upper_snake(name):
    return snake(name).upper()


def strip_ob(name):
    """Drop the legacy `Ob` prefix of a C++ type name (ObFoo -> Foo, OBFoo stays)."""
    if len(name) > 2 and name.startswith('Ob') and (name[2].isupper() or name[2].isdigit()):
        return name[2:]
    return name


def short_qname(q):
    """oceanbase::sql::ObFoo -> sql::ObFoo (the oceanbase:: prefix carries no information)."""
    return q[len('oceanbase::'):] if q.startswith('oceanbase::') else q


def clean(v):
    return L._clean(v)
