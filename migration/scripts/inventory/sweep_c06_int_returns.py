#!/usr/bin/env python3
"""c06-int-returns: every first-party function whose canonical return type is `int` (int, int32_t and
typedefs of them), one row per canonical declaration (RULEBOOK §2.1 last row, §5.2 #6, §5.3).

Writes the sweep migration/inventory/sweep/c06-int-returns.tsv, its counts.tsv row, and the decided shard
migration/inventory.c06-int-returns.tsv (kit 7 columns) for the rows the rules below decide with
certainty (classification ERRCODE or VALUE, status confirmed); every other row is residue for agents.

Sites: astdb function records (function, method, function_template, conversion) with
canonical_return_type `int`/`const int` whose canonical declaration is not in a generated file (those
belong to the generators, RULEBOOK 0.13); plus a regex fallback over the first-party files that no
translation unit parses (files.tsv tus=0).

Body = the comment/string-stripped brace block that ends on the definition record's end_line; for a
definition made inside a #define (body not in the source file) the function's block is looked up by name
in that macro's replacement text and, recursively, in the macros it invokes (depth 4).  Lambdas and
local classes inside a body are blanked.
  error-code variable  `int v = <error constant>` / `INIT_SUCC(v)` / `ret` when the body uses OB_FAIL or
                       OB_SUCC or invokes a first-party macro whose text declares `int ret = OB_SUCCESS`
                       (e.g. EVAL_ARG) / `tmp_ret` when OB_TMP_FAIL is used / any v assigned or compared
                       to an error constant.  Error constants = DEFINE_* names of ob_errno.def, the
                       parser's OB_PARSER_* codes, OK_.
  value variable       a local `int v = <integer literal>` that is not an error-code variable, is only
                       assigned literals, arithmetic, comparisons or libc compare/length calls, and is never
                       a bare call argument (an out-parameter write would be invisible).
Decision rules (the rule id is in the pretag and in the shard evidence):
  E1 ret-var       every return is an error-code variable or an error constant (or a ternary of them),
                   at least one variable; no error-code variable is assigned a literal, arithmetic or
                   libc-call value, updated with ++/+=, or compared with 0 by < > <= >=   -> ERRCODE
  E2 errno-const   every return is an error constant                                -> ERRCODE
  E3 delegate      every return is as in E1/E2 or a single call whose callee resolves (below) to
                   functions that are all decided ERRCODE (fixpoint)                -> ERRCODE
  S  serialize     serialize/deserialize/serialize_/deserialize_ with the unis (buf, len, pos)
                   parameters: RULEBOOK §2.11 maps them to `-> ObResult`            -> ERRCODE
  V1 value-expr    no error constant, OB_FAIL/OB_SUCC/OB_TMP_FAIL/INIT_SUCC, error-code variable or POSIX
                   errno name (EINVAL, -ENOENT, ...) in the body; every return is a literal, an
                   arithmetic/comparison/logical expression, a ternary of value arms, sizeof, a libc
                   compare/length call or a value variable; not all returns are the literal 0  -> VALUE
  V2 value-type    conversion operator to int, or a return type spelled through a value typedef of int
                   (ObLength, obstr_size_t, pid_t, int_type) with no error constant in the body -> VALUE
  V3 delegate      as V1, plus single calls whose callee resolves to functions all decided VALUE -> VALUE
  F  family        a virtual method without a visible body whose override family (methods linked by
                   `overrides`) has bodies that are all decided the same way         -> that class
  Callee resolution (E3/V3): `f(..)`/`this->f(..)` in a method -> methods `f` of the class and its bases,
  else free functions `f`; `C::f(..)` -> methods of class C (+bases) or functions `…C::f`;
  `x.f(..)`/`x->f(..)` with x a parameter, field or local whose declared type names a class -> methods `f`
  of that class (+bases), `->` only through a raw pointer; any other receiver -> every database function
  named f.  Candidates are filtered by arity (defaults and variadics counted); unqualified calls of libc
  names, `std::`/`::` calls and names without candidates stay unresolved.
  A family with both ERRCODE and VALUE members is not decided at all (pretag family-conflict), and the
  propagation is recomputed without it.
Pretag (a hint): `errcode:<rule>` / `value:<rule>` for decided rows, `errcode?:delegate=<callees>`,
`value?:delegate=…`, `mixed:<reason>`, `no-body:<reason>`; then `virtual`, `family-conflict`,
`posix-errno`, `name=<value-ish name>`, `type=<typedef>`, `unit=<disposition>` (units not translated).
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3blib as S  # noqa: E402
import astdb  # noqa: E402

L = S.L
CAT = 'c06-int-returns'
SHARD = os.path.join(S.REPO, 'migration', 'inventory.c06-int-returns.tsv')
KIT_HEADER = ['file', 'symbol', 'source_construct', 'classification', 'target_translation', 'evidence', 'status']

INT_TYPES = ('int', 'const int')
ERRNO = S.errno_names()
POSIX_ERRNO = set('''E2BIG EACCES EADDRINUSE EADDRNOTAVAIL EAFNOSUPPORT EAGAIN EALREADY EBADF EBADMSG EBUSY ECANCELED
ECHILD ECONNABORTED ECONNREFUSED ECONNRESET EDEADLK EDESTADDRREQ EDOM EDQUOT EEXIST EFAULT EFBIG EHOSTUNREACH EIDRM
EILSEQ EINPROGRESS EINTR EINVAL EIO EISCONN EISDIR ELOOP EMFILE EMLINK EMSGSIZE EMULTIHOP ENAMETOOLONG ENETDOWN
ENETRESET ENETUNREACH ENFILE ENOBUFS ENODATA ENODEV ENOENT ENOEXEC ENOLCK ENOLINK ENOMEM ENOMSG ENOPROTOOPT ENOSPC
ENOSR ENOSTR ENOSYS ENOTCONN ENOTDIR ENOTEMPTY ENOTRECOVERABLE ENOTSOCK ENOTSUP ENOTTY ENXIO EOPNOTSUPP EOVERFLOW
EOWNERDEAD EPERM EPIPE EPROTO EPROTONOSUPPORT EPROTOTYPE ERANGE EROFS ESPIPE ESRCH ESTALE ETIME ETIMEDOUT ETXTBSY
EWOULDBLOCK EXDEV'''.split())
RX_POSIX = re.compile(r'\b(E[A-Z0-9]{2,})\b')
LIBC = set('''abort abs access accept alarm atoi atol atoll bind calloc chdir chmod chown clock_gettime close closedir
connect creat dup dup2 epoll_create epoll_ctl epoll_wait execv execvp exit fallocate fchmod fclose fcntl fdatasync
feof ferror fflush fgetc fgets fileno flock fopen fork fprintf fputs fread free freopen fscanf fseek fstat fsync ftell
ftruncate fwrite getaddrinfo getcwd getenv gethostname getpid getrlimit getrusage getsockopt gettid gettimeofday
inet_pton ioctl isalnum isalpha isdigit isspace kill labs link listen llabs lseek lstat madvise malloc memcmp mkdir
mkstemp mlock mmap mprotect munmap nanosleep open opendir pipe poll posix_memalign prctl pread printf pthread_create
pthread_join pthread_kill pthread_mutex_lock pthread_mutex_unlock pthread_getname_np pthread_setname_np pwrite
raise read readdir readlink realloc recv recvfrom remove rename rmdir sched_getaffinity sched_setaffinity select
sem_post sem_wait send sendto setenv setrlimit setsockopt sigaction sigaddset sigemptyset signal sigprocmask
sigwait snprintf socket sprintf sscanf stat statfs statvfs strcasecmp strcmp strerror strlen strncasecmp strncmp
strtol strtoll strtoul strtoull symlink syscall sysconf system tolower toupper truncate umask uname unlink usleep
vsnprintf wait waitpid write writev sigtimedwait getpagesize backtrace'''.split())
RX_ERR_TOKEN = re.compile(r'\b(OB_[A-Z0-9_]+|OK_)\b')
RX_RETURN = re.compile(r'\breturn\b')
RX_INIT_SUCC = re.compile(r'\bINIT_SUCC\s*\(\s*(\w+)\s*\)')
RX_INT_DECL = re.compile(r'\b(?:int|int32_t)\s+(\w+)\s*(=\s*([^;,{}]*)|\(\s*([^;{}]*?)\)|\{\s*([^;{}]*?)\})\s*[;,]')
RX_ASSIGN = r'(?<![\w.>])(?:this\s*->\s*)?%s\s*=(?!=)\s*([^;]*);'
RX_COMPOUND = r'(?<![\w.>])(?:this\s*->\s*)?%s\s*(?:[-+*/%%&|^]|<<|>>)=|(?:\+\+|--)\s*%s\b|\b%s\s*(?:\+\+|--)'
RX_SIGN_CMP = r'(?<![\w.>])%s\s*(?:<|>|<=|>=)\s*0\b|\b0\s*(?:<|>|<=|>=)\s*%s\b'
RX_CMP_ERR = r'(?:\b%s\s*[!=]=\s*(?:(?:::)?\w+::)*(?:OB_[A-Z0-9_]+|OK_)\b|\b(?:OB_[A-Z0-9_]+|OK_)\s*[!=]=\s*%s\b)'
RX_OB_FAIL = re.compile(r'\bOB_(?:FAIL|SUCC)\s*\(')
RX_OB_TMP_FAIL = re.compile(r'\bOB_TMP_FAIL\s*\(')
RX_ERRCONST = re.compile(r'^(?:::)?(?:\w+\s*::\s*)*(OB_[A-Z0-9_]+|OK_)$')
RX_LITERAL = re.compile(r"^[-+]?\s*(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*$|^' '$|^(?:true|false)$")
RX_ZERO = re.compile(r'^(?:0|0[xX]0+|false)[uUlL]*$')
RX_IDENT = re.compile(r'^(?:this\s*->\s*)?[A-Za-z_]\w*$')
RX_CAST = re.compile(r'^(?:static_cast\s*<\s*(?:const\s+)?(?:int|int32_t|unsigned|unsigned\s+int|uint32_t|int64_t|'
                     r'uint64_t|long|int8_t|uint8_t|int16_t|uint16_t|char)\s*>\s*(?=\()|\(\s*(?:int|int32_t|unsigned|'
                     r'int64_t|uint32_t)\s*\)|(?:int|int32_t)\s*(?=\())')
VALUE_CALLS = {'memcmp', 'strcmp', 'strncmp', 'strcasecmp', 'strncasecmp', 'wcscmp', 'MEMCMP', 'STRCMP', 'STRNCMP',
               'STRCASECMP', 'STRNCASECMP', 'strlen', 'STRLEN', 'abs', 'labs', 'llabs', 'toupper', 'tolower',
               'isspace', 'isdigit', 'isalpha', 'isalnum', 'isupper', 'islower', 'isxdigit'}
VALUE_NAME = re.compile(r'(?:^|_)(?:compare\w*|cmp|cmp_\w+|\w+_cmp|hash\w*|size|count|cnt|len|length|num|width|'
                        r'height|depth|level|precision|scale|idx|index|offset|pos|priority|weight|score|'
                        r'to_string|get_\w*(?:count|cnt|num|len|length|size|idx|index|level|type|id))$')
ERRISH = re.compile(r'(?:^|_)(?:ret|err|errno|errcode|code|status|rc)(?:_|$)', re.I)
VALUE_TYPEDEFS = {'ObLength', 'common::ObLength', 'obstr_size_t', 'pid_t', 'int_type'}
SERIAL_NAMES = ('serialize', 'deserialize', 'serialize_', 'deserialize_')
CAST_NAMES = ('sizeof', 'static_cast', 'reinterpret_cast', 'const_cast', 'dynamic_cast', 'decltype', 'alignof')

ERRCODE_TT = ('-> ObResult (RULEBOOK §2.1: `int ret = OB_SUCCESS` -> `let mut ret: ObResult = Ok(())`; `return ret;` '
              'unchanged; `return OB_X;` -> `return Err(OB_X);`; output parameters stay `&mut`)')
VALUE_TT = ('-> i32 (a value, not an error code: RULEBOOK §2.1 last row; int/int32_t -> i32 per §2.12; '
            'the returned expressions are kept)')


# ---------------------------------------------------------------- expression helpers
def _balanced(s):
    d = 0
    for ch in s:
        if ch in '([{':
            d += 1
        elif ch in ')]}':
            d -= 1
            if d < 0:
                return False
    return d == 0


def norm_expr(e):
    e = ' '.join(e.split())
    while e.startswith('(') and e.endswith(')') and _balanced(e[1:-1]):
        e = e[1:-1].strip()
    return e


def _strip_groups(e):
    """e with the contents of balanced (...) [...] {...} groups removed (the brackets kept)."""
    out, d = [], 0
    for ch in e:
        if ch in '([{':
            if d == 0:
                out.append(ch)
            d += 1
        elif ch in ')]}':
            d -= 1
            if d == 0:
                out.append(ch)
        elif d == 0:
            out.append(ch)
    return ''.join(out)


_RX_TPL = re.compile(r'(?<=[A-Za-z0-9_])\s*<[^<>;|&?]*>')


def _drop_templates(t):
    while True:
        u = _RX_TPL.sub(' ', t)
        if u == t:
            return t
        t = u


def _top_text(e):
    return _drop_templates(_strip_groups(e)).replace('->', ' . ').replace('::', ' ')


def has_binary_operator(e):
    """True if e has a top-level operator other than a leading unary * or & (deref/address-of)."""
    if '->*' in e or '.*' in e:
        return False
    t = _top_text(e).strip()
    t2 = re.sub(r'^[*&]+\s*', '', t)
    return bool(re.search(r'[-+*/%<>!&|^~?=]', t2))


def call_parts(e):
    """(name, nargs, prefix) if e is exactly one call (a postfix chain ending in `name(args)`), else None.
    prefix = the text before the name ('' / 'this->' / 'x.' / 'x->' / 'C::' / 'a.b()->' ...)."""
    if not e.endswith(')'):
        return None
    d = 0
    k = len(e) - 1
    while k >= 0:
        ch = e[k]
        if ch == ')':
            d += 1
        elif ch == '(':
            d -= 1
            if d == 0:
                break
        k -= 1
    if k <= 0:
        return None
    args = e[k + 1:-1].strip()
    head = e[:k].rstrip()
    if head.endswith('>'):
        dd, j = 0, len(head) - 1
        while j >= 0:
            if head[j] == '>':
                dd += 1
            elif head[j] == '<':
                dd -= 1
                if dd == 0:
                    break
            j -= 1
        if j < 0:
            return None
        head = head[:j].rstrip()
    m = re.search(r'(~?[A-Za-z_]\w*)$', head)
    if not m:
        return None
    name = m.group(1)
    if name in CAST_NAMES:
        return None
    prefix = head[:m.start()].rstrip()
    if prefix:
        if not re.search(r'(?:\.|->|::)$', prefix):
            return None
        t = _drop_templates(_strip_groups(prefix)).replace('->', '.')
        if re.search(r'[-+/%!&|^~?=,<>]', t) or re.search(r'(?<![(])\*', t):
            return None
    nargs = 0 if not args else _strip_groups(args).count(',') + 1
    return (name, nargs, prefix.replace(' ', ''))


def _lit_value(e):
    e = e.replace(' ', '').rstrip('uUlL')
    if e in ('true',):
        return 1
    if e in ('false',):
        return 0
    if e == "''":
        return 'char'
    try:
        return int(e, 0)
    except ValueError:
        return e


STATUS_LITERALS = {0, -1, -2, -3, 1}


def status_like(body, vals):
    """True if every value return is a literal or a literal-only value variable and the literal set looks like a
    C status (subset of {0, -1, -2, -3, 1} without both a negative value and 1), so it is not proven a value."""
    lits = set()
    for _loc, k, e in vals:
        kind = k[1]
        if kind in ('literal', 'zero'):
            lits.add(_lit_value(norm_expr(re.sub(r'^\s*(?:static_cast<[^>]*>|\(\s*\w+\s*\))\s*', '', e))))
        elif kind == 'arith' and '?' in _strip_groups(e):
            arms = ternary_arms(norm_expr(e))
            if not arms or not all(RX_LITERAL.match(a) for a in arms):
                return False
            lits |= {_lit_value(a) for a in arms}
        elif kind == 'valvar':
            name = re.sub(r'^this\s*->\s*', '', e)
            if name in body.valvar_strong:
                return False
            lits |= getattr(body, 'valvar_lits', {}).get(name, set())
        else:
            return False
    if not lits <= STATUS_LITERALS:
        return False
    return not (any(isinstance(x, int) and x < 0 for x in lits) and 1 in lits)


def ternary_arms(e):
    d = 0
    q = c = -1
    for i, ch in enumerate(e):
        if ch in '([{':
            d += 1
        elif ch in ')]}':
            d -= 1
        elif ch == '?' and d == 0 and q < 0:
            q = i
        elif ch == ':' and d == 0 and q >= 0 and e[i - 1] != ':' and (i + 1 >= len(e) or e[i + 1] != ':'):
            c = i
            break
    if q < 0 or c < 0:
        return None
    return [norm_expr(e[q + 1:c]), norm_expr(e[c + 1:])]


# ---------------------------------------------------------------- macros that declare `ret`
def ret_macros():
    """name -> 'file:line' of first-party macros whose text declares `int ret = OB_SUCCESS` or INIT_SUCC(ret)."""
    rx_ret = re.compile(r'\bint\s+ret\s*=\s*(?:(?:::)?\w+::)*(?:OB_SUCCESS|OK_)\b|\bINIT_SUCC\s*\(\s*ret\s*\)')
    out = {}
    for name, defs in S.macro_index().items():
        for md in defs:
            if rx_ret.search(md.text):
                out.setdefault(name, '%s:%d' % (md.file, md.line))
    return out


# ---------------------------------------------------------------- body analysis
class Body(object):
    """content: stripped text of the brace block (content[0] == '{'); braces: same-length text used for
    brace/lambda detection; line_fn(rel_offset) -> (file, line)."""

    def __init__(self, content, braces, line_fn, retmacros):
        self.line_fn = line_fn
        ranges = S.inner_ranges_text(braces)
        self.own = S.blank_ranges(content, 0, ranges)
        self.returns = []          # (loc, expr)
        for m in RX_RETURN.finditer(self.own):
            j = self.own.find(';', m.end())
            if j < 0:
                continue
            expr = norm_expr(self.own[m.end():j])
            if expr:
                self.returns.append((line_fn(m.start()), expr))
        self.errconsts = sorted({t for t in RX_ERR_TOKEN.findall(self.own) if t in ERRNO})
        self.posix = sorted({t for t in RX_POSIX.findall(self.own) if t in POSIX_ERRNO})
        self.uses_ob_fail = bool(RX_OB_FAIL.search(self.own))
        self.uses_tmp_fail = bool(RX_OB_TMP_FAIL.search(self.own))
        self.ret_macro = None
        if re.search(r'\bret\b', self.own):
            for mm in re.finditer(r'\b([A-Z][A-Z0-9_]+)\b', self.own):
                if mm.group(1) in retmacros:
                    self.ret_macro = (mm.group(1), retmacros[mm.group(1)], line_fn(mm.start()))
                    break
        self.errvars = {}
        self.valvars = set()
        self.valvar_strong = set()     # value variables assigned a non-literal value expression
        self.literals = set()          # integer literals returned directly or through value variables
        self.bad_errvar_assign = []
        self.locals = {}
        self._vars()

    def _vars(self):
        own = self.own
        decls = {}
        for m in RX_INT_DECL.finditer(own):
            init = (m.group(3) or m.group(4) or m.group(5) or '').strip()
            decls.setdefault(m.group(1), (init, m.start()))
        for m in RX_INIT_SUCC.finditer(own):
            self.errvars.setdefault(m.group(1), self.line_fn(m.start()))
        for v, (init, off) in decls.items():
            if any(t in ERRNO for t in RX_ERR_TOKEN.findall(init)):
                self.errvars.setdefault(v, self.line_fn(off))
        if self.uses_ob_fail and 'ret' not in self.errvars:
            self.errvars['ret'] = None
        if self.ret_macro and 'ret' not in self.errvars:
            self.errvars['ret'] = self.ret_macro[2]
        if self.uses_tmp_fail and 'tmp_ret' not in self.errvars:
            self.errvars['tmp_ret'] = None
        for v in sorted(set(decls) | {'ret', 'tmp_ret'}):
            if v in self.errvars:
                continue
            for m in re.finditer(RX_ASSIGN % re.escape(v), own):
                if self.is_errconst_expr(norm_expr(m.group(1))):
                    self.errvars[v] = self.line_fn(m.start())
                    break
            if v not in self.errvars and re.search(RX_CMP_ERR % (re.escape(v), re.escape(v)), own):
                self.errvars[v] = None
        for v in self.errvars:
            ev = re.escape(v)
            for m in re.finditer(RX_ASSIGN % ev, own):
                rhs = norm_expr(m.group(1))
                if self.is_value_assign(rhs):
                    self.bad_errvar_assign.append((v, self.line_fn(m.start()), rhs))
            for m in re.finditer(RX_COMPOUND % (ev, ev, ev), own):
                self.bad_errvar_assign.append((v, self.line_fn(m.start()), m.group(0)))
            for m in re.finditer(RX_SIGN_CMP % (ev, ev), own):
                self.bad_errvar_assign.append((v, self.line_fn(m.start()), m.group(0)))
        for v, (init, off) in decls.items():
            if v in self.errvars or not RX_LITERAL.match(norm_expr(init) or 'x'):
                continue
            ok = True
            lits = {_lit_value(norm_expr(init))}
            strong = False
            for m in re.finditer(RX_ASSIGN % re.escape(v), own):
                rhs = norm_expr(m.group(1))
                if RX_LITERAL.match(rhs):
                    lits.add(_lit_value(rhs))
                elif self.is_value_assign(rhs):
                    strong = True
                else:
                    ok = False
                    break
            if re.search(RX_COMPOUND % ((re.escape(v),) * 3), own):
                strong = True
            if ok and re.search(r'[(,]\s*&?\s*%s\s*[,)]' % re.escape(v), own):
                ok = False
            if ok:
                self.valvars.add(v)
                if strong:
                    self.valvar_strong.add(v)
                self.valvar_lits = getattr(self, 'valvar_lits', {})
                self.valvar_lits[v] = lits

    def is_errconst_expr(self, e):
        m = RX_ERRCONST.match(e)
        if m:
            return m.group(1) in ERRNO
        arms = ternary_arms(e) if '?' in e else None
        if arms:
            return all(self.is_errconst_expr(a) or (RX_IDENT.match(a) and a in self.errvars) for a in arms)
        m = re.match(r'^(?:OB_E|E)\s*\([^()]*\)\s*(?:(?:::)?\w+::)*(OB_[A-Z0-9_]+)$', e)
        return bool(m and m.group(1) in ERRNO)

    def is_value_assign(self, rhs):
        if RX_LITERAL.match(rhs):
            return not RX_ZERO.match(rhs)
        cp = call_parts(rhs)
        if cp and cp[0] in LIBC:
            return True
        return self.value_kind(rhs, nested=True) not in (None, 'zero')

    def value_kind(self, e, nested=False):
        """literal / zero / arith / sizeof / libc / valvar, or None."""
        e = norm_expr(e)
        m = RX_CAST.match(e)
        if m:
            return self.value_kind(e[m.end():], nested)
        if RX_LITERAL.match(e):
            return 'zero' if RX_ZERO.match(e) else 'literal'
        if e.startswith('sizeof'):
            return 'sizeof'
        if '{' in e:
            return None
        if any(t in ERRNO for t in RX_ERR_TOKEN.findall(e)):
            return None
        cp = call_parts(e)
        if cp is not None:
            return 'libc' if cp[0] in VALUE_CALLS and not cp[2] else None
        if RX_IDENT.match(e):
            name = re.sub(r'^this\s*->\s*', '', e)
            return 'valvar' if not nested and name in self.valvars else None
        if '?' in _strip_groups(e):
            arms = ternary_arms(e)
            if not arms:
                return None
            ks = [self.value_kind(a, nested) for a in arms]
            if all(k is not None for k in ks):
                return 'zero' if all(k == 'zero' for k in ks) else 'arith'
            return None
        if has_binary_operator(e):
            return 'arith'
        return None

    def ret_kind(self, e):
        """('err', detail) / ('val', kind) / ('call', (name, nargs, prefix)) / ('other', expr)."""
        m = RX_ERRCONST.match(e)
        if m and m.group(1) in ERRNO:
            return ('err', 'const')
        name = re.sub(r'^this\s*->\s*', '', e)
        if RX_IDENT.match(e) and name in self.errvars:
            return ('err', 'var')
        if '?' in _strip_groups(e):
            arms = ternary_arms(e)
            if arms:
                ks = [self.ret_kind(a) for a in arms]
                if all(k[0] == 'err' for k in ks):
                    return ('err', 'ternary')
        vk = self.value_kind(e)
        if vk:
            return ('val', vk)
        cp = call_parts(e)
        if cp is not None:
            return ('call', cp)
        return ('other', e)


def classify(body):
    """(cls, rule, locs, calls): cls in errcode / value / delegate-err / delegate-val / mixed."""
    if not body.returns:
        return ('mixed', 'no-return', [], [])
    kinds = [(loc, body.ret_kind(e), e) for loc, e in body.returns]
    errs = [k for k in kinds if k[1][0] == 'err']
    vals = [k for k in kinds if k[1][0] == 'val']
    calls = [k for k in kinds if k[1][0] == 'call']
    others = [k for k in kinds if k[1][0] == 'other']
    ret_locs = sorted({loc for loc, _k, _e in kinds})
    decl_locs = sorted({loc for loc in body.errvars.values() if loc})
    if others:
        idents = [re.sub(r'^this\s*->\s*', '', e) for _l, _k, e in others if RX_IDENT.match(e)]
        if len(idents) == len(others):
            errish = any(ERRISH.search(n) for n in idents)
            return ('mixed', 'returns-%s%s' % ('errish-ident=' if errish else 'ident=', idents[0]), ret_locs, [])
        return ('mixed', 'other-expr', ret_locs, [])
    no_err_signal = not body.errconsts and not body.uses_ob_fail and not body.uses_tmp_fail and \
        not body.errvars and 'INIT_SUCC' not in body.own
    call_list = sorted({k[1][1] for k in calls})
    if errs and not vals and not calls:
        if body.bad_errvar_assign:
            return ('mixed', 'errvar-value-use', ret_locs, [])
        if any(k[1][1] in ('var', 'ternary') for k in errs):
            return ('errcode', 'E1', decl_locs + ret_locs, [])
        return ('errcode', 'E2', ret_locs, [])
    if vals and not errs:
        if body.posix:
            return ('mixed', 'posix-errno', ret_locs, [])
        if not no_err_signal:
            return ('mixed', 'value-returns-with-error-signals', ret_locs, [])
        if all(k[1][1] == 'zero' for k in vals) and not calls:
            return ('mixed', 'zero-only', ret_locs, [])
        if status_like(body, vals):
            return ('mixed', 'status-literals', ret_locs, [])
        if not calls:
            return ('value', 'V1', ret_locs, [])
        return ('delegate-val', 'V3', ret_locs, call_list)
    if calls and not vals:
        if body.bad_errvar_assign:
            return ('mixed', 'errvar-value-use', ret_locs, [])
        if not errs and body.posix:
            return ('mixed', 'posix-errno', ret_locs, [])
        return ('delegate-err', 'E3', decl_locs + ret_locs, call_list)
    return ('mixed', 'error-and-value-returns' if errs and vals else 'calls-and-values', ret_locs, [])


def file_body(path, ob, cb, retmacros):
    st = L.structural_text(path)
    return Body(L.stripped(path)[ob:cb + 1], st[ob:cb + 1], lambda rel: (path, L.line_of(path, ob + rel)), retmacros)


# ---------------------------------------------------------------- bodies inside #define text
def invocation_args(d, macro):
    """Arguments of the macro invocation that made definition d (stripped source at d's line/col), or None."""
    path = d['file']
    text = L.stripped(path)
    lines_n = text.count('\n') + 1
    if d['line'] > lines_n:
        return None
    start = L.line_start_offset(path, d['line'])
    m = re.compile(r'\b%s\s*\(' % re.escape(macro)).search(text, start, start + 4000)
    if not m:
        return None
    i = m.end() - 1
    depth = 0
    j = i
    while j < len(text):
        ch = text[j]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                break
        j += 1
    if j >= len(text):
        return None
    inner = text[i + 1:j]
    args, depth, cur = [], 0, []
    for ch in inner:
        if ch in '([{<' and ch != '<':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            args.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    args.append(''.join(cur).strip())
    return args


def substitute_params(text, params, args):
    """Replace whole-word macro parameters by the invocation arguments (no ## / # processing)."""
    if not params or args is None:
        return text
    mapping = {}
    for k, p in enumerate(params):
        if p in ('...',) or p.endswith('...'):
            mapping['__VA_ARGS__' if p == '...' else p[:-3]] = ', '.join(args[k:])
            break
        if k < len(args):
            mapping[p] = args[k]
    if not mapping:
        return text
    rx = re.compile(r'(?<![\w#])(%s)(?![\w#])' % '|'.join(re.escape(p) for p in sorted(mapping, key=len, reverse=True)))
    # keep offsets line-stable: substitutions never contain newlines after ' '.join
    return rx.sub(lambda m: ' '.join(mapping[m.group(1)].split()), text)


def macro_bodies(macro, fname, retmacros, depth=0, seen=None, d=None):
    """Bodies of functions named fname defined in the replacement text of `macro` (or of macros it invokes).
    For the outermost macro the parameters are replaced by the arguments of the invocation that made d."""
    seen = seen if seen is not None else set()
    if macro in seen or depth > 4:
        return []
    seen.add(macro)
    out = []
    idx = S.macro_index()
    rx_name = re.compile(r'(?<![\w~.>])%s\s*(?:<[^(){};]*>)?\s*\(' % re.escape(fname))
    args = invocation_args(d, macro) if (d is not None and depth == 0) else None
    for md in idx.get(macro, []):
        text = substitute_params(md.text, md.params, args) if args is not None else md.text
        for m in rx_name.finditer(text, md.body_off):
            i = m.end() - 1
            d = 0
            while i < len(text):
                if text[i] == '(':
                    d += 1
                elif text[i] == ')':
                    d -= 1
                    if d == 0:
                        break
                i += 1
            k = i + 1
            tail = re.match(r'(?:\s|const\b|override\b|final\b|noexcept\b|volatile\b|&&|&|OB_INLINE\b)*', text[k:])
            k += tail.end()
            if k >= len(text) or text[k] != '{':
                continue
            d = 0
            j = k
            while j < len(text):
                if text[j] == '{':
                    d += 1
                elif text[j] == '}':
                    d -= 1
                    if d == 0:
                        break
                j += 1
            if j >= len(text):
                continue
            block = text[k:j + 1]
            out.append(Body(block, block, (lambda md, text, k: lambda rel: (md.file, md.line + text.count('\n', 0, k + rel)))(
                md, text, k), retmacros))
        if not out:
            for inv in sorted(set(re.findall(r'\b([A-Z_][A-Z0-9_]{2,})\s*\(', text[md.body_off:]))):
                if inv in idx and inv != macro:
                    out.extend(macro_bodies(inv, fname, retmacros, depth + 1, seen))
    return out


# ---------------------------------------------------------------- symbols
def params_sig(rec):
    ps = [p.get('canonical_type') or p.get('type') or '?' for p in rec.get('params', [])]
    s = ', '.join(ps)
    if rec.get('is_variadic'):
        s = s + (', ...' if s else '...')
    return s.replace('oceanbase::', '')


def symbol_of(rec):
    s = '%s(%s)' % (S.short_qname(rec['qname']), params_sig(rec))
    if rec.get('is_const'):
        s += ' const'
    if rec.get('ref_qual'):
        s += ' ' + str(rec['ref_qual'])
    return s


CONSTRUCT = {'function': 'function returning int', 'method': 'method returning int',
             'function_template': 'function template returning int', 'conversion': 'conversion to int'}


def is_serial_shape(rec):
    if rec['name'] not in SERIAL_NAMES:
        return False
    ps = [p.get('canonical_type', '') for p in rec.get('params', [])]
    return len(ps) == 3 and ps[0] in ('char *', 'const char *') and \
        ps[1] in ('long long', 'const long long') and ps[2] == 'long long &'


# ---------------------------------------------------------------- callee resolution
class Resolver(object):
    def __init__(self, db):
        self.db = db
        self.funcs_by_name = collections.defaultdict(list)    # name -> canonical function records (one per usr)
        seen = set()
        for r in db.records:
            if r['kind'] in astdb.FUNC_KINDS and r['usr'] not in seen and r['kind'] not in ('ctor', 'dtor'):
                seen.add(r['usr'])
                c = db.canonical(r['usr'])
                if c is not None:
                    self.funcs_by_name[c['name']].append(c)
        self.records_by_name = collections.defaultdict(list)
        self.records_by_qname = {}
        for r in db.records:
            if r['kind'] in astdb.RECORD_KINDS and r.get('is_definition'):
                self.records_by_name[r['name']].append(r['usr'])
                self.records_by_qname.setdefault(r['qname'], r['usr'])
        self._methods = {}
        self._fields = {}

    def methods_named(self, rec_usr, name):
        key = rec_usr
        if key not in self._methods:
            ms = collections.defaultdict(list)
            try:
                for m in self.db.methods(rec_usr, inherited=True):
                    ms[m['name']].append(m)
            except ValueError:
                pass
            self._methods[key] = ms
        return self._methods[key].get(name, [])

    def field_type(self, rec_usr, fname):
        if rec_usr not in self._fields:
            fs = {}
            for f in self.db.fields(rec_usr, inherited=True):
                fs.setdefault(f['name'], f.get('canonical_type') or f.get('type') or '')
            self._fields[rec_usr] = fs
        return self._fields[rec_usr].get(fname)

    def record_of_type(self, t, via_arrow):
        """Record USR named by a (canonical) type string; '->' needs a raw pointer, '.' a non-pointer."""
        t = re.sub(r'\b(?:const|volatile|struct|class|union|typename)\b', '', t).strip()
        is_ptr = t.endswith('*') and t.count('*') == 1
        is_ref = t.endswith('&')
        base = t.rstrip('*&').strip()
        if via_arrow and not is_ptr:
            return None
        if not via_arrow and is_ptr:
            return None
        base = re.sub(r'<.*>$', '', base).strip()
        if not base or is_ref and base.endswith('*'):
            return None
        if base in self.records_by_qname:
            return self.records_by_qname[base]
        short = base.split('::')[-1]
        us = self.records_by_name.get(short, [])
        return us[0] if len(us) == 1 else None

    def candidates(self, call, ctx):
        """Function records a call can resolve to, or None if unresolved."""
        name, nargs, prefix = call
        cands = None
        if prefix.startswith('std::') or prefix == '::':
            return None
        if prefix in ('', 'this->'):
            if ctx.get('class_usr'):
                ms = self.methods_named(ctx['class_usr'], name)
                if ms:
                    cands = ms
            if cands is None:
                if prefix == '' and name in LIBC:
                    return None
                cands = [c for c in self.funcs_by_name.get(name, []) if c['kind'] in ('function', 'function_template')]
        elif prefix.endswith('::') and re.match(r'^(?:\w+(?:<[^<>]*>)?::)+$', prefix):
            qual = re.sub(r'<[^<>]*>', '', prefix[:-2])
            last = qual.split('::')[-1]
            us = self.records_by_name.get(last, [])
            if us:
                cands = []
                for u in us:
                    cands.extend(self.methods_named(u, name))
            else:
                cands = [c for c in self.funcs_by_name.get(name, []) if c['qname'].endswith('%s::%s' % (qual, name))]
        else:
            m = re.match(r'^(?:this->)?([A-Za-z_]\w*)(\.|->)$', prefix)
            ru = None
            if m:
                var, arrow = m.group(1), m.group(2) == '->'
                t = ctx['params'].get(var)
                if t is None and ctx.get('class_usr'):
                    t = self.field_type(ctx['class_usr'], var)
                if t is None:
                    t = ctx['locals'](var)
                if t:
                    ru = self.record_of_type(t, arrow)
            if ru is not None:
                cands = self.methods_named(ru, name)
            else:
                cands = list(self.funcs_by_name.get(name, []))
        out = []
        keys = set()
        for c in cands or []:
            ps = c.get('params', [])
            req = sum(1 for p in ps if not p.get('has_default'))
            var = c.get('is_variadic') or any('...' in (p.get('type') or '') for p in ps) or \
                c['kind'] == 'function_template' and any((p.get('type') or '').endswith('&&') for p in ps)
            if req <= nargs and (var or nargs <= len(ps)):
                if c['kind'] != 'function':
                    # methods() lists the receiver's own methods first, then bases nearest first: an identical
                    # signature further up is hidden or overridden
                    k = (c['name'],) + sig_key(c)
                    if k in keys:
                        continue
                    keys.add(k)
                out.append(c)
        return out or None


RX_LOCAL_DECL = r'(?:^|[;{}(,]\s*)((?:const\s+)?[A-Za-z_][\w:]*(?:\s*<[^;(){}]*?>)?(?:\s*::\s*\w+)*\s*[*&]?)\s*\b%s\s*(?:[;=,)]|\(|\{|\[)'


def make_ctx(d, body_text, db):
    params = {}
    for p in d.get('params', []) or []:
        if p.get('name'):
            params[p['name']] = p.get('canonical_type') or p.get('type') or ''
    pu = d.get('parent_usr')
    cls = pu if pu and db.definition(pu, astdb.RECORD_KINDS) is not None else None
    cache = {}

    def local(var):
        if var not in cache:
            m = re.search(RX_LOCAL_DECL % re.escape(var), body_text)
            cache[var] = m.group(1).strip() if m and m.group(1).split()[-1] not in ('return', 'else', 'case', 'new', 'delete') else None
        return cache[var]

    return {'params': params, 'class_usr': cls, 'locals': local}


# ---------------------------------------------------------------- main analysis
def analyse(db, retmacros):
    res = Resolver(db)
    usrs = sorted({r['usr'] for r in db.records if r['kind'] in astdb.FUNC_KINDS and
                   r.get('canonical_return_type') in INT_TYPES})
    rows = collections.OrderedDict()
    for u in usrs:
        c = db.canonical(u)
        if c is None or c.get('generated'):
            continue
        defs = sorted((r for r in db.get(u) if r.get('is_definition') and not r.get('generated')),
                      key=lambda r: (r['file'], r['line']))
        info = {'rec': c, 'defs': defs, 'base_cls': None, 'base_rule': None, 'ev': [], 'calls': [],
                'pending': None, 'typedef': None, 'has_body': False, 'posix': False}
        rt = re.sub(r'^const\s+', '', (c.get('return_type') or '').strip())
        bodies, macro_only = [], False
        for d in defs:
            sp = S.body_span(d)
            bs = []
            if sp is not None:
                bs = [(file_body(d['file'], sp[0], sp[1], retmacros), d)]
            elif d.get('in_macro') and d.get('macro'):
                macro_only = True
                bs = [(b, d) for b in macro_bodies(d['macro'], c['name'], retmacros, d=d)]
            if not bs:
                bodies.append(None)
                continue
            for b, dd in bs:
                cls, rule, locs, calls = classify(b)
                text = b.own
                ctx = make_ctx(dd, text, db)
                bodies.append((cls, rule, locs, [(cp, ctx) for cp in calls], b))
        info['has_body'] = any(x is not None for x in bodies)
        info['posix'] = any(x is not None and x[4].posix for x in bodies)
        if c.get('kind') == 'conversion':
            info.update(base_cls='value', base_rule='V2', typedef='operator int')
        elif is_serial_shape(c):
            info.update(base_cls='errcode', base_rule='S', has_body=True)
        elif not defs:
            info['base_rule'] = 'no-body:pure-virtual' if c.get('is_pure') else 'no-body:declaration-only'
        elif not info['has_body']:
            d0 = defs[0]
            info['base_rule'] = ('no-body:macro=%s' % d0.get('macro')) if macro_only else 'no-body:unlocated'
            info['macro_body'] = macro_only
        else:
            got = [x for x in bodies if x is not None]
            kinds = {x[0] for x in got}
            if len(kinds) > 1 or len(got) != len(bodies):
                info['base_rule'] = 'mixed:definitions-disagree'
            else:
                cls, rule = got[0][0], got[0][1]
                ev = sorted({e for x in got for e in x[2]})
                calls = [cc for x in got for cc in x[3]]
                anyconst = any(x[4].errconsts for x in got)
                if rt in VALUE_TYPEDEFS and not anyconst:
                    info.update(base_cls='value', base_rule='V2', ev=ev, typedef=rt)
                elif cls in ('errcode', 'value'):
                    info.update(base_cls=cls, base_rule=rule, ev=ev)
                elif cls in ('delegate-err', 'delegate-val'):
                    direct = any(x[4].ret_kind(e)[0] in ('err', 'val') for x in got for _l, e in x[4].returns)
                    info.update(pending=cls, base_rule=rule, ev=ev, calls=calls, direct_returns=direct)
                else:
                    info.update(base_rule='mixed:' + rule, ev=ev)
            if any(d.get('in_macro') for d in defs) and macro_only:
                info['macro_text_body'] = True
        if info['base_cls'] is None and info['base_rule'] and info['base_rule'].startswith('no-body') and \
                rt in VALUE_TYPEDEFS:
            info.update(base_cls='value', base_rule='V2', typedef=rt)
        rows[u] = info
    # resolve the calls of pending rows once (candidate usr lists)
    for u, info in rows.items():
        if info['pending']:
            resolved = []
            for (cp, ctx) in info['calls']:
                cs = res.candidates(cp, ctx)
                resolved.append((cp, sorted({x['usr'] for x in cs}) if cs else None))
            info['resolved'] = resolved
    return rows


def sig_key(rec):
    return (tuple(p.get('canonical_type') or p.get('type') or '' for p in rec.get('params', []) or []),
            bool(rec.get('is_const')), rec.get('ref_qual') or '')


def template_override_links(rows, db):
    """(derived_method_usr, base_method_usr) pairs for virtual methods whose overriders libclang does not link
    (members of class templates and classes with dependent bases): a method of a derived record (db.derived,
    which follows template bases) with the same name, canonical parameter types, const and ref qualifier."""
    by_parent_name = collections.defaultdict(list)
    for u, info in rows.items():
        r = info['rec']
        if r.get('parent_usr'):
            by_parent_name[(r['parent_usr'], r['name'])].append(u)
    linked = set()
    for u, info in rows.items():
        for o in info['rec'].get('overrides', []) or []:
            linked.add((u, o))
    derived_cache = {}
    out = []
    for u, info in rows.items():
        r = info['rec']
        if not r.get('is_virtual') or not r.get('parent_usr'):
            continue
        pu = r['parent_usr']
        if pu not in derived_cache:
            try:
                derived_cache[pu] = sorted(db.derived(pu))
            except ValueError:
                derived_cache[pu] = []
        key = sig_key(r)
        for du in derived_cache[pu]:
            for m in by_parent_name.get((du, r['name']), ()):
                if m != u and (m, u) not in linked and sig_key(rows[m]['rec']) == key:
                    out.append((m, u))
    return out


def families(rows, db=None):
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = []
    for u, info in rows.items():
        for o in info['rec'].get('overrides', []) or []:
            if o in rows:
                links.append((u, o))
    if db is not None:
        links.extend(template_override_links(rows, db))
    for u, o in links:
        parent.setdefault(u, u)
        parent.setdefault(o, o)
        ra, rb = find(u), find(o)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb
    fam = collections.defaultdict(list)
    for u in sorted(parent):
        fam[find(u)].append(u)
    return fam


def decide(rows, db=None):
    fams = families(rows, db)
    fam_of = {}
    for root, ms in fams.items():
        for m in ms:
            fam_of[m] = root
    blocked = set()
    while True:
        cls = {u: (info['base_cls'] if fam_of.get(u) not in blocked else None) for u, info in rows.items()}
        rule = {u: (info['base_rule'] if cls[u] else None) for u, info in rows.items()}
        extra = {}
        changed = True
        while changed:
            changed = False
            for u, info in rows.items():
                if cls[u] is not None or not info['pending'] or fam_of.get(u) in blocked:
                    continue
                want = 'errcode' if info['pending'] == 'delegate-err' else 'value'
                ok = True
                evidence = bool(info.get('direct_returns'))
                for cp, cands in info['resolved']:
                    others = [x for x in (cands or []) if x != u]      # a recursive call returns what u returns
                    if not cands or any(cls.get(x) != want for x in others):
                        ok = False
                        break
                    evidence = evidence or bool(others)
                ok = ok and evidence
                if ok:
                    cls[u], rule[u] = want, ('E3' if want == 'errcode' else 'V3')
                    changed = True
            for root, ms in fams.items():
                if root in blocked:
                    continue
                with_body = [m for m in ms if rows[m]['has_body']]
                if not with_body or any(cls[m] is None for m in with_body):
                    continue
                got = {cls[m] for m in with_body}
                if len(got) != 1:
                    continue
                c0 = got.pop()
                for m in ms:
                    if cls[m] is None and not rows[m]['has_body'] and not rows[m].get('macro_body'):
                        cls[m], rule[m] = c0, 'F'
                        extra[m] = with_body
                        changed = True
        new_blocked = set(blocked)
        for root, ms in fams.items():
            if len({cls[m] for m in ms if cls[m]}) > 1:
                new_blocked.add(root)
        if new_blocked == blocked:
            return cls, rule, extra, blocked, fam_of
        blocked = new_blocked


def callee_label(cp):
    name, nargs, prefix = cp
    return '%s%s/%d' % (prefix, name, nargs)


def main():
    db = S.load_db(astdb.FUNC_KINDS + astdb.RECORD_KINDS + ('field',))
    retmacros = ret_macros()
    rows = analyse(db, retmacros)
    cls, rule, extra, blocked, fam_of = decide(rows, db)
    sweep, ref = [], {}
    stats = collections.Counter()
    for u, info in rows.items():
        c = info['rec']
        pre = []
        if cls[u]:
            pre.append('%s:%s' % (cls[u], rule[u]))
        elif info['pending']:
            unresolved = [callee_label(cp) for cp, cands in info['resolved'] if not cands]
            labels = [callee_label(cp) for cp, _c in info['resolved']]
            pre.append('%s:delegate=%s%s' % ('errcode?' if info['pending'] == 'delegate-err' else 'value?',
                                             ','.join(sorted(set(labels))[:5]),
                                             (' (unresolved %s)' % ','.join(sorted(set(unresolved))[:3])) if unresolved else ''))
        elif fam_of.get(u) in blocked:
            pre.append('family-conflict')
            if info['base_cls'] or info['base_rule']:
                pre.append('own=%s' % ('%s:%s' % (info['base_cls'], info['base_rule']) if info['base_cls']
                                       else info['base_rule']))
        else:
            br = info['base_rule'] or 'mixed:unclassified'
            if info['base_cls']:
                br = '%s:%s' % (info['base_cls'], info['base_rule'])
            pre.append(br)
        if c.get('is_virtual') or c.get('overrides'):
            pre.append('virtual')
        if info['posix']:
            pre.append('posix-errno')
        if info['typedef'] and info['typedef'] != 'operator int':
            pre.append('type=%s' % info['typedef'])
        if VALUE_NAME.search(c['name']):
            pre.append('name=%s' % c['name'])
        if info.get('macro_text_body'):
            pre.append('body-in-macro=%s' % info['defs'][0].get('macro'))
        un = S.unit_of(c['file'])
        if un is not None and un.disposition != 'translate':
            pre.append('unit=%s' % un.disposition)
        row = [c['file'], symbol_of(c), CONSTRUCT.get(c['kind'], c['kind'] + ' returning int'), c['line'],
               '; '.join(pre)]
        sweep.append(row)
        ref[u] = row
        stats[cls[u] or 'residue'] += 1
    seen = collections.Counter()
    for r in sorted(sweep, key=lambda r: (r[0], r[3], r[1])):
        k = (r[0], r[1], r[2])
        seen[k] += 1
        if seen[k] > 1:
            r[1] = '%s#%d' % (r[1], seen[k])
    fb = fallback_rows(retmacros)
    n = L.finish(CAT, sweep + fb, note(stats, len(fb)))
    shard = []
    for u, info in rows.items():
        if not cls[u]:
            continue
        row = ref[u]
        un = S.unit_of(info['rec']['file'])
        shard.append((row[0], row[1], row[2], cls[u].upper(), target_text(cls[u], un),
                      evidence_text(info, rule[u], extra.get(u), rows), 'confirmed'))
    shard.sort(key=lambda r: (r[0], r[1], r[2]))
    L.write_tsv(SHARD, KIT_HEADER, shard)
    print('shard: %s (%d rows); decided %s' % (L.rel(SHARD), len(shard), dict(sorted(stats.items()))))
    return n


def target_text(c, un):
    tt = ERRCODE_TT if c == 'errcode' else VALUE_TT
    if un is not None and un.disposition != 'translate':
        tt += '; unit disposition %s: the replacing module, shim or C code keeps this contract' % un.disposition
    return tt


def _locs(ev):
    by_file = collections.OrderedDict()
    for f, ln in sorted(set(ev)):
        by_file.setdefault(f, []).append(str(ln))
    return '; '.join('%s:%s' % (f, ','.join(lns[:12]) + (',...' if len(lns) > 12 else '')) for f, lns in by_file.items())


def evidence_text(info, rule, fam_members, rows):
    c = info['rec']
    locs = _locs(info.get('ev') or [])
    mac = ' (function block in the text of macro %s)' % info['defs'][0].get('macro') if info.get('macro_text_body') else ''
    if rule == 'E1':
        return 'rule E1: error-code variable declared and every return at %s%s' % (locs, mac)
    if rule == 'E2':
        return 'rule E2: only error constants returned, at %s%s' % (locs, mac)
    if rule in ('E3', 'V3'):
        calls = sorted({callee_label(cp) for cp, _c in info['resolved']})
        n = sum(len(cs or []) for _cp, cs in info['resolved'])
        return 'rule %s: returns at %s%s; returned calls %s resolve to %d candidate function(s), all %s' % (
            rule, locs, mac, ', '.join(calls), n, 'ERRCODE' if rule == 'E3' else 'VALUE')
    if rule == 'V1':
        return 'rule V1: value expressions only (no error constant, OB_FAIL/OB_SUCC, ret or errno name), returns at %s%s' % (
            locs, mac)
    if rule == 'V2':
        if c.get('kind') == 'conversion':
            return 'rule V2: conversion operator to int at %s:%d' % (c['file'], c['line'])
        return 'rule V2: return type %s (value typedef of int) at %s:%d%s' % (
            info['typedef'], c['file'], c['line'], ('; returns at ' + locs) if locs else '')
    if rule == 'S':
        d = info['defs'][0] if info['defs'] else c
        m = (' (body made by macro %s)' % d.get('macro')) if d.get('in_macro') else ''
        return 'rule S: unis serialize/deserialize(buf, len, pos) is `-> ObResult` by RULEBOOK §2.11; definition at %s:%d%s' % (
            d['file'], d['line'], m)
    if rule == 'F':
        ms = fam_members or []
        ex = rows[ms[0]]['rec'] if ms else None
        return ('rule F: no body at %s:%d; the %d members of its override family with bodies are all decided the same way '
                '(e.g. %s at %s:%d)') % (c['file'], c['line'], len(ms), S.short_qname(ex['qname']) if ex else '?',
                                         ex['file'] if ex else '?', ex['line'] if ex else 0)
    return 'rule %s at %s' % (rule, locs)


# ---------------------------------------------------------------- regex fallback (files no TU parses)
RX_FB_DECL = re.compile(r'^[ \t]*((?:(?:static|virtual|inline|OB_INLINE|extern|friend|constexpr)\s+)*)'
                        r'(int|int32_t)\s+((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*\(([^;{}]*?)\)\s*(const\s*)?'
                        r'(?:override\s*|final\s*|noexcept\s*)*(;|\{|=\s*0\s*;)', re.M)


def fallback_rows(retmacros):
    rows = []
    for p in S.tus0_files():
        text = L.stripped(p)
        st = L.structural_text(p)
        for m in RX_FB_DECL.finditer(text):
            name = re.sub(r'\s+', '', m.group(3))
            if name in ('return', 'else', 'if', 'while', 'for', 'switch', 'case'):
                continue
            if L.in_function(p, m.start(3)):
                continue
            line = L.line_of(p, m.start(3))
            cls = L.enclosing_class(p, m.start(3))
            q = ('%s::%s' % (cls, name)) if cls and cls != '<anonymous>' and '::' not in name else name
            params = ' '.join(m.group(4).split())
            sym = '%s(%s)%s' % (q, params, ' const' if m.group(5) else '')
            tag = 'regex-fallback(tus=0)'
            if m.group(6) == '{':
                ob = m.end() - 1
                depth, i = 0, ob
                n = len(st)
                while i < n:
                    ch = st[i]
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if i < n:
                    cl, ru, _locs, calls = classify(file_body(p, ob, i, retmacros))
                    lab = {'delegate-err': 'errcode?', 'delegate-val': 'value?'}.get(cl, cl)
                    tag += '; %s:%s' % (lab, ('delegate=' + ','.join(callee_label(x) for x in calls[:5])) if calls else ru)
            else:
                tag += '; no-body:declaration'
            un = S.unit_of(p)
            if un is not None and un.disposition != 'translate':
                tag += '; unit=%s' % un.disposition
            rows.append([p, sym, 'int-returning declaration (regex; file not parsed)', line, tag])
    seen = collections.Counter()
    for r in rows:
        k = (r[0], r[1], r[2])
        seen[k] += 1
        if seen[k] > 1:
            r[1] = '%s#%d' % (r[1], seen[k])
    return rows


def note(stats, nfb):
    dec = stats['errcode'] + stats['value']
    return ('functions, methods and templates with canonical return type int (AST, one row per canonical declaration, '
            'generated files excluded) + %d regex rows for files no TU parses; script-decided %d (ERRCODE %d, VALUE %d) '
            'in migration/inventory.c06-int-returns.tsv; residue for agents %d AST rows + %d regex rows'
            % (nfb, dec, stats['errcode'], stats['value'], stats['residue'], nfb))


if __name__ == '__main__':
    main()
