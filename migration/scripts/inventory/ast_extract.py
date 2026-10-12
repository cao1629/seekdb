#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ast_extract.py -- libclang declaration database for phase 1c (gap inventory).

Walks every first-party translation unit of the ORIGINAL C++ release build
(build_release/compile_commands.json, unity build) with libclang and writes a
deduplicated declaration database:

  build_release/inventory-ast/tu/<tu>.jsonl       per-TU shard (one JSON object per declaration)
  build_release/inventory-ast/tu/<tu>.meta.json   per-TU diagnostics/timing; written last = shard complete
  build_release/inventory-ast/tu/<tu>.log         stdout/stderr of the TU's parser process
  build_release/inventory-ast/decls.jsonl         merged, deduplicated, sorted
  build_release/inventory-ast/errors.tsv          per-TU diagnostic counts and first fatal/error message
  build_release/inventory-ast/files.tsv           per-file coverage: TUs including it, records in it
  build_release/inventory-ast/merge-info.json     merge counters (shard lines, records, variants)
  build_release/inventory-ast/summary.json        counts used by the README validation section
  build_release/inventory-ast/run.log             progress log

Run with the libclang venv interpreter in isolated mode, from the repository root:

  PY=/Users/meizhi/.claude/jobs/3985849d/tmp/venv-libclang/bin/python
  $PY -I migration/scripts/inventory/ast_extract.py run --jobs 8   # parse (resumable) + merge + stats
  $PY -I migration/scripts/inventory/ast_extract.py merge          # merge shards again (deterministic)
  $PY -I migration/scripts/inventory/ast_extract.py stats          # recompute summary.json from decls.jsonl
  $PY -I migration/scripts/inventory/ast_extract.py list           # TU names, main files, skip reasons
  $PY -I migration/scripts/inventory/ast_extract.py args --tu NAME # libclang argument vector of one TU
  $PY -I migration/scripts/inventory/ast_extract.py parse-one --tu NAME   # (internal) one TU -> shard
  $PY -I migration/scripts/inventory/ast_extract.py probe FILE [clang args]  # (debug) one file inside the repo

`merge` and `stats` only need the standard library; `run`/`parse-one` need the
`clang` Python bindings (pip package libclang==18.1.1).  The record schema is
documented in migration/scripts/inventory/README.md, section "ast_extract.py".
"""

import argparse
import concurrent.futures
import datetime
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time

SCHEMA = 'ast_extract/1'

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
BUILD = os.path.join(ROOT, 'build_release')
CC_JSON = os.path.join(BUILD, 'compile_commands.json')
OUT = os.path.join(BUILD, 'inventory-ast')
TU_DIR = os.path.join(OUT, 'tu')
DEPMAP = os.path.join(ROOT, 'migration', 'depmap')

# libclang parse options (CXTranslationUnit_Flags).  Function bodies are NOT skipped:
# with CXTranslationUnit_SkipFunctionBodies (0x40) clang_isCursorDefinition() returns 0
# for every function whose body was skipped (tested), so definitions would be lost.
# KeepGoing (0x200) is not used either: it turns fatal errors into plain errors and
# would hide them from errors.tsv.
OPT_VISIT_IMPLICIT_ATTRIBUTES = 0x2000          # shows #pragma pack's implicit attribute
OPT_IGNORE_NON_ERRORS_FROM_INCLUDED = 0x4000    # warnings from headers are noise here
PARSE_OPTIONS = OPT_VISIT_IMPLICIT_ATTRIBUTES | OPT_IGNORE_NON_ERRORS_FROM_INCLUDED

TU_TIMEOUT_S = 3600

# anonymous struct/union USRs ('...@Ua', '...@Sa') carry no location: every anonymous
# record of one scope gets the same USR, so the extractor appends file:line:col.
ANON_USR_RE = re.compile(r'@[SUE]a$')
TOKEN_RE = re.compile(rb'[A-Za-z_][A-Za-z0-9_]*|~|\S')
TAG_KEYWORDS = ('struct', 'class', 'union', 'enum')
# spellings of the weak attribute; OB_WEAK_SYMBOL is defined in src/oblib/lib/utility/ob_macro_utils.h:20
# (third-party spellings: grpc/impl/codegen/port_platform.h, google/protobuf/port_def.inc,
# absl/base/attributes.h; reachable from generated protobuf code)
WEAK_TOKENS = ('weak', '__weak__', 'OB_WEAK_SYMBOL', 'GPR_ATTRIBUTE_WEAK', 'PROTOBUF_ATTRIBUTE_WEAK',
               'ABSL_ATTRIBUTE_WEAK')
TEMPLATE_PARAM_RE = re.compile(r'^(?:const |volatile )*type-parameter-\d+-\d+$')
PRAGMA_PACK_RE = re.compile(r'\s*#\s*pragma\s+pack\s*\(([^)]*)\)')


# --------------------------------------------------------------------------- utilities

def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def rel(path):
    """Repo-relative form of an absolute path (None when outside the repo)."""
    p = os.path.normpath(path)
    if p == ROOT or p.startswith(ROOT + os.sep):
        return p[len(ROOT) + 1:]
    return None


_ROOT_PREFIX = ROOT + '/'


def strip_root(s):
    """Remove the absolute repo prefix from strings such as
    '(anonymous struct at /abs/repo/src/x.h:3:5)' so the output is machine independent."""
    if s and _ROOT_PREFIX in s:
        return s.replace(_ROOT_PREFIX, '')
    return s


def dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def write_atomic(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- file classes

class FileClasses:
    """First-party / generated classification of repo files, from the dependency map.

    first-party (generated=0): every path in migration/depmap/nodes.tsv (git-tracked,
        hand-written, part of the CMake build).
    generated (generated=1):
        * paths in migration/depmap/excluded.tsv with class `generated` (checked-in
          generator output: ob_errno.h, system-variable tables, protoc output, ...);
        * build-time generator output that is not checked in and that the depmap lists
          as `configure: ...` in unresolved.tsv: bison/flex output written into src/
          (src/sql/parser/*_tab.[ch], *_lex.[ch], type_name.c, src/pl/parser/...), the
          inner-table schema and sqlite virtual tables under build_release/generated/,
          and generated sources under build_release/src/ (ob_version.cpp,
          syspack_source.cpp).  Any other untracked path under src/ falls in here too;
          the merge lists every such path in merge-info.json so it can be checked.
    excluded: third-party and unbuilt paths of excluded.tsv, everything outside src/
        and build_release/{generated,src}, unity wrappers under CMakeFiles/, and
        build_release/rust-target/ plus rust/ (cxx bridges of code that is already Rust).
    """

    def __init__(self):
        self.nodes = set()
        with open(os.path.join(DEPMAP, 'nodes.tsv'), encoding='utf-8') as f:
            next(f)
            for line in f:
                p = line.rstrip('\n').split('\t')[0]
                if p:
                    self.nodes.add(p)
        self.excluded = {}
        with open(os.path.join(DEPMAP, 'excluded.tsv'), encoding='utf-8') as f:
            next(f)
            for line in f:
                cols = line.rstrip('\n').split('\t')
                if len(cols) >= 2:
                    self.excluded[cols[0]] = cols[1]

    def classify(self, relpath):
        """0 = first-party, 1 = generated (recorded with generated=1), None = not recorded."""
        if relpath is None:
            return None
        if relpath in self.nodes:
            return 0
        if relpath.startswith('src/'):
            c = self.excluded.get(relpath)
            if c == 'generated':
                return 1
            if c is not None:          # third_party / unbuilt
                return None
            return 1                   # untracked build output written into src/
        if relpath.startswith('build_release/generated/'):
            return 1
        if relpath.startswith('build_release/src/') and '/CMakeFiles/' not in relpath:
            return 1
        return None

    def is_untracked_src(self, relpath):
        return (relpath is not None and relpath.startswith('src/') and relpath not in self.nodes
                and relpath not in self.excluded)


# --------------------------------------------------------------------------- TUs and args

def sanitize_name(relpath):
    s = relpath
    if s.startswith('build_release/'):
        s = s[len('build_release/'):]
    s = s.replace('/CMakeFiles/', '/').replace('.dir/Unity/', '/')
    return re.sub(r'[^A-Za-z0-9._-]+', '__', s)


def load_entries(fc=None):
    with open(CC_JSON, encoding='utf-8') as f:
        cc = json.load(f)
    fc = fc or FileClasses()
    ents = []
    seen = {}
    for e in cc:
        path = os.path.normpath(os.path.join(e['directory'], e['file']))
        r = rel(path)
        name = sanitize_name(r if r else path)
        if name in seen:
            raise SystemExit('duplicate TU name %s (%s, %s)' % (name, seen[name], path))
        seen[name] = path
        skip = None
        cls = fc.excluded.get(r)
        if cls == 'third_party':
            skip = 'third-party main file (depmap excluded.tsv)'
        elif cls == 'unbuilt':
            skip = 'unbuilt main file (depmap excluded.tsv)'
        lang = 'c' if path.endswith('.c') else 'c++'
        ents.append({'name': name, 'file': path, 'rel': r, 'directory': e['directory'],
                     'command': e['command'], 'output': e.get('output'), 'skip': skip, 'lang': lang})
    ents.sort(key=lambda x: x['name'])
    return ents


_DROP_EXACT = {'-c', '-Werror', '-fcolor-diagnostics', '-fno-color-diagnostics',
               '-ffunction-sections', '-fdata-sections', '-MD', '-MMD', '-MP'}
_DROP_WITH_ARG = {'-o', '-MF', '-MT', '-MQ'}
_DROP_PREFIX = ('-fdebug-prefix-map=', '-ffile-prefix-map=', '-fmacro-prefix-map=', '-Werror=',
                '-g')


def resource_dir_for(compiler):
    """The resource dir of the compiler that really built the TU (clang 17 builtin headers)."""
    base = os.path.normpath(os.path.join(os.path.dirname(compiler), '..', 'lib', 'clang'))
    cands = sorted(glob.glob(os.path.join(base, '*')))
    cands = [c for c in cands if os.path.isdir(os.path.join(c, 'include'))]
    return cands[-1] if cands else None


def libclang_args(ent):
    """Compile command -> libclang argument vector (input file passed separately)."""
    argv = shlex.split(ent['command'])
    compiler = argv[0]
    src = ent['file']
    out = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in _DROP_WITH_ARG:
            i += 2
            continue
        if a in _DROP_EXACT or a.startswith(_DROP_PREFIX):
            i += 1
            continue
        if not a.startswith('-') and os.path.normpath(os.path.join(ent['directory'], a)) == src:
            i += 1
            continue
        out.append(a)
        i += 1
    rd = resource_dir_for(compiler)
    if rd:
        out += ['-resource-dir', rd]
    out += ['-ferror-limit=0', '-Wno-unknown-warning-option', '-working-directory', ent['directory']]
    return out


def extractor_digest():
    """Hash of this script's code (docstrings and comments excluded): a shard made by
    another version of the extractor is stale and `run` parses its TU again."""
    import ast
    with open(os.path.abspath(__file__), encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        body = getattr(node, 'body', None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and body \
                and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree).encode('utf-8')).hexdigest()[:16]


def args_digest(args):
    return hashlib.sha256('\0'.join(args).encode('utf-8')).hexdigest()[:16]


# --------------------------------------------------------------------------- extraction

class Extractor:
    """Walks one parsed translation unit and collects first-party declaration records."""

    def __init__(self, ci, tu, fc):
        self.ci = ci
        self.tu = tu
        self.fc = fc
        self.lib = ci.conf.lib
        K = ci.CursorKind
        T = ci.TypeKind
        self.K = K
        self.T = T
        self.recs = {}
        self.dups = 0
        self.dup_conflicts = 0
        self.fcache = {}          # CXFile pointer -> (relpath, generated) | None
        self.scope_cache = {}     # parent key -> (qname, usr)
        self.vt_cache = {}        # record usr -> has vtable
        self.pack_cache = {}      # relpath -> [(start, end, N)]
        self.untracked = set()
        self.src_cache = {}       # relpath -> source lines (bytes), for macro detection
        self._init_ctypes()

        self.CONTAINERS = {K.NAMESPACE, K.LINKAGE_SPEC, K.UNEXPOSED_DECL}
        self.REC_KIND = {K.STRUCT_DECL: 'struct', K.CLASS_DECL: 'class', K.UNION_DECL: 'union',
                         K.CLASS_TEMPLATE: 'class_template',
                         K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION: 'partial_specialization'}
        self.FUNC_KIND = {K.FUNCTION_DECL: 'function', K.CXX_METHOD: 'method', K.CONSTRUCTOR: 'ctor',
                          K.DESTRUCTOR: 'dtor', K.CONVERSION_FUNCTION: 'conversion',
                          K.FUNCTION_TEMPLATE: 'function_template'}
        self.TPARAM_KIND = {K.TEMPLATE_TYPE_PARAMETER: 'type', K.TEMPLATE_NON_TYPE_PARAMETER: 'non_type',
                            K.TEMPLATE_TEMPLATE_PARAMETER: 'template'}
        self.TAG = {K.STRUCT_DECL: 'struct', K.CLASS_DECL: 'class', K.UNION_DECL: 'union'}
        self.ACCESS = {ci.AccessSpecifier.PUBLIC: 'public', ci.AccessSpecifier.PROTECTED: 'protected',
                       ci.AccessSpecifier.PRIVATE: 'private'}
        self.STORAGE = {ci.StorageClass.EXTERN: 'extern', ci.StorageClass.STATIC: 'static',
                        ci.StorageClass.PRIVATEEXTERN: 'private_extern',
                        ci.StorageClass.REGISTER: 'register'}
        self.LINKAGE = {ci.LinkageKind.NO_LINKAGE: 'none', ci.LinkageKind.INTERNAL: 'internal',
                        ci.LinkageKind.UNIQUE_EXTERNAL: 'unique_external', ci.LinkageKind.EXTERNAL: 'external'}
        self.TLS = {ci.TLSKind.DYNAMIC: 'dynamic', ci.TLSKind.STATIC: 'static'}
        self.SIMPLE_TYPE = {
            T.LVALUEREFERENCE: 'lvalue_ref', T.RVALUEREFERENCE: 'rvalue_ref', T.RECORD: 'record',
            T.ENUM: 'enum', T.TYPEDEF: 'typedef', T.CONSTANTARRAY: 'constant_array',
            T.INCOMPLETEARRAY: 'incomplete_array', T.VARIABLEARRAY: 'array',
            T.DEPENDENTSIZEDARRAY: 'array', T.MEMBERPOINTER: 'member_pointer',
            T.FUNCTIONPROTO: 'function', T.FUNCTIONNOPROTO: 'function', T.AUTO: 'auto',
            T.VECTOR: 'vector', T.COMPLEX: 'complex', T.BLOCKPOINTER: 'block_pointer',
            T.DEPENDENT: 'dependent', T.INVALID: 'invalid'}
        for name in ('ATOMIC', 'EXTVECTOR'):
            if hasattr(T, name):
                self.SIMPLE_TYPE[getattr(T, name)] = name.lower() if name != 'EXTVECTOR' else 'vector'
        self.EXC_SPEC = {}
        # (unevaluated / uninstantiated / unparsed say nothing about the declaration; omitted)
        for name in ('DYNAMIC_NONE', 'DYNAMIC', 'MS_ANY', 'BASIC_NOEXCEPT', 'COMPUTED_NOEXCEPT', 'NOTHROW'):
            if hasattr(ci.ExceptionSpecificationKind, name):
                self.EXC_SPEC[getattr(ci.ExceptionSpecificationKind, name)] = name.lower()

    # ---- ctypes helpers for C API calls that cindex 18 does not wrap
    def _init_ctypes(self):
        import ctypes
        ci = self.ci
        lib = self.lib
        self.ctypes = ctypes
        Cursor = ci.Cursor
        lib.clang_getOverriddenCursors.argtypes = [Cursor, ctypes.POINTER(ctypes.POINTER(Cursor)),
                                                   ctypes.POINTER(ctypes.c_uint)]
        lib.clang_getOverriddenCursors.restype = None
        lib.clang_disposeOverriddenCursors.argtypes = [ctypes.POINTER(Cursor)]
        lib.clang_disposeOverriddenCursors.restype = None
        for fn in ('clang_Cursor_isFunctionInlined', 'clang_Cursor_isAnonymousRecordDecl',
                   'clang_Cursor_isVariadic', 'clang_isVirtualBase'):
            getattr(lib, fn).argtypes = [Cursor]
            getattr(lib, fn).restype = ctypes.c_uint
        lib.clang_getUnqualifiedType.argtypes = [ci.Type]
        lib.clang_getUnqualifiedType.restype = ci.Type

    def unqualified(self, t):
        u = self.lib.clang_getUnqualifiedType(t)
        u._tu = t._tu
        return u

    def overridden(self, c):
        ctypes = self.ctypes
        ptr = ctypes.POINTER(self.ci.Cursor)()
        n = ctypes.c_uint()
        self.lib.clang_getOverriddenCursors(c, ctypes.byref(ptr), ctypes.byref(n))
        res = []
        if ptr:
            try:
                for i in range(n.value):
                    oc = ptr[i]
                    oc._tu = c._tu
                    res.append(oc.get_usr())
            finally:
                self.lib.clang_disposeOverriddenCursors(ptr)
        return sorted(set(res))

    # ---- locations
    def addr(self, p):
        """Address held by a CXFile handle (cindex c_object_p), used as cache key."""
        return self.ctypes.cast(p, self.ctypes.c_void_p).value or 0

    def where(self, c):
        """(relpath, generated, line, col, file key) of the expansion location, None when
        the location is not in a recorded (first-party or generated) file."""
        f, line, col, _ = c.location._get_instantiation()
        if f is None:
            return None
        key = self.addr(f.obj)
        info = self.fcache.get(key, False)
        if info is False:
            r = rel(f.name)
            g = self.fc.classify(r)
            info = None if g is None else (r, g)
            if info is not None and self.fc.is_untracked_src(r):
                self.untracked.add(r)
            self.fcache[key] = info
        if info is None:
            return None
        return info[0], info[1], line, col, key

    def token_at(self, rpath, line, col):
        """First token of the source text at (line, col) (1-based, byte columns)."""
        lines = self.src_cache.get(rpath)
        if lines is None:
            try:
                with open(os.path.join(ROOT, rpath), 'rb') as f:
                    lines = f.read().split(b'\n')
            except OSError:
                lines = []
            self.src_cache[rpath] = lines
        if not (0 < line <= len(lines)):
            return None
        m = TOKEN_RE.match(lines[line - 1], col - 1)
        return m.group(0).decode('latin-1') if m else None

    def end_line(self, c, w):
        """Last line of the declaration's extent (closing brace of a class or function body)
        when it ends in the same file, else None."""
        f, eline, _, _ = c.extent.end._get_instantiation()
        if f is None or self.addr(f.obj) != w[4]:
            return None
        return eline

    def decl_text(self, c, w):
        """Source bytes from the declaration's location to the end of its extent (same
        file only), or None."""
        end = c.extent.end
        f, eline, ecol, _ = end._get_instantiation()
        if f is None or self.addr(f.obj) != w[4]:
            return None
        lines = self.src_cache.get(w[0])
        if lines is None:
            self.token_at(w[0], w[2], w[3])
            lines = self.src_cache.get(w[0]) or []
        if not (0 < w[2] <= eline <= len(lines)):
            return None
        if w[2] == eline:
            return lines[w[2] - 1][w[3] - 1:ecol]
        return b'\n'.join([lines[w[2] - 1][w[3] - 1:]] + lines[w[2]:eline - 1] + [lines[eline - 1][:ecol]])

    def has_brace_body(self, c, w):
        """Whether the declaration's source text contains '{' (None when unknown)."""
        text = self.decl_text(c, w)
        return None if text is None else b'{' in text

    def macro_name(self, w, name, kind):
        """Name of the macro whose expansion produced the declaration, or None.

        libclang reports the expansion location (the macro invocation) for declarations that
        come out of a macro, and clang_getSpellingLocation() cannot see into macro bodies
        (it returns the file location).  So: if the token at the reported location is not the
        declaration's own name, the declaration was macro-generated and that token is the
        (outermost) macro's name."""
        tok = self.token_at(w[0], w[2], w[3])
        if tok is None:
            return None
        tagged = kind in ('class', 'struct', 'union', 'enum', 'class_template', 'partial_specialization')
        if not name:                                    # anonymous record / unnamed bit-field
            return (None if tok in TAG_KEYWORDS else tok) if tagged else None
        if tagged and tok in TAG_KEYWORDS:
            return None
        if kind == 'alias' and tok == 'using':          # alias templates point at 'using'
            return None
        if name.startswith('operator') and (len(name) == 8 or not (name[8].isalnum() or name[8] == '_')):
            return None if tok == 'operator' else tok
        if name.startswith('~'):
            return None if tok == '~' else tok
        return None if tok == name else tok

    # ---- names
    def rec_usr(self, c):
        u = c.get_usr()
        if ANON_USR_RE.search(u):
            # anonymous struct/union (or enum without enumerators): clang gives every such
            # record in one scope the same USR, so make it unique with its location.
            f, line, col, _ = c.location._get_instantiation()
            r = rel(f.name) if f is not None else None
            u = '%s@%s:%d:%d' % (u, r or '?', line, col)
        return u

    def component(self, c):
        K = self.K
        k = c.kind
        if k == K.NAMESPACE:
            return c.spelling or '(anonymous namespace)'
        if k in self.REC_KIND or k == K.ENUM_DECL:
            sp = strip_root(c.spelling)
            if k == K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION:
                return strip_root(c.displayname)
            if k in (K.STRUCT_DECL, K.CLASS_DECL, K.UNION_DECL) and c.get_num_template_arguments() > 0:
                return strip_root(c.displayname)
            return sp
        if k in self.FUNC_KIND:
            return c.spelling + self.template_args_text(c)
        return strip_root(c.spelling)

    def template_args_text(self, c):
        n = c.get_num_template_arguments()
        if n is None or n <= 0:
            return ''
        TAK = self.ci.TemplateArgumentKind
        parts = []
        for i in range(n):
            try:
                ak = c.get_template_argument_kind(i)
                if ak == TAK.TYPE:
                    parts.append(strip_root(c.get_template_argument_type(i).spelling))
                elif ak == TAK.INTEGRAL:
                    parts.append(str(c.get_template_argument_value(i)))
                else:
                    parts.append('?')
            except ValueError:
                parts.append('?')
        return '<' + ', '.join(parts) + '>'

    def scope(self, p):
        """(qualified name, usr, is_record) of a semantic parent cursor (('', '', False) for the TU)."""
        K = self.K
        if p is None:
            return ('', '', False)
        k = p.kind
        if k == K.TRANSLATION_UNIT or k.is_invalid():
            return ('', '', False)
        if k in (K.LINKAGE_SPEC, K.UNEXPOSED_DECL):
            return self.scope(p.semantic_parent)
        usr = self.rec_usr(p) if (k in self.REC_KIND or k == K.ENUM_DECL) else p.get_usr()
        key = (k.value, usr)
        r = self.scope_cache.get(key)
        if r is None:
            pq = self.scope(p.semantic_parent)[0]
            comp = self.component(p)
            r = (pq + '::' + comp if pq else comp, usr, k in self.REC_KIND)
            self.scope_cache[key] = r
        return r

    # ---- types
    def classify(self, t):
        T = self.T
        k = t.kind
        guard = 0
        while k == T.ELABORATED and guard < 8:
            t = t.get_named_type()
            k = t.kind
            guard += 1
        if k == T.POINTER:
            pk = t.get_pointee().get_canonical().kind
            return 'function_pointer' if pk in (T.FUNCTIONPROTO, T.FUNCTIONNOPROTO) else 'pointer'
        s = self.SIMPLE_TYPE.get(k)
        if s:
            return s
        if 2 <= k.value <= 40:
            return 'builtin'
        if k == T.UNEXPOSED:
            # libclang 18 reports UsingType sugar (a name found through a using-declaration,
            # which depends on what an earlier .cpp of the unity TU declared) and dependent
            # types as UNEXPOSED.  Classify through the declaration, then the canonical type,
            # so the result does not depend on the TU.
            K = self.K
            ct = t.get_canonical()
            cs = ct.spelling
            if TEMPLATE_PARAM_RE.match(cs):
                return 'template_param'
            d = t.get_declaration()
            if self.valid(d):
                dk = d.kind
                if dk in (K.TYPEDEF_DECL, K.TYPE_ALIAS_DECL, K.TYPE_ALIAS_TEMPLATE_DECL):
                    return 'typedef'
                if dk in (K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
                    return 'template_spec'
                if dk in self.TAG:
                    return 'template_spec' if d.get_num_template_arguments() > 0 else 'record'
                if dk == K.ENUM_DECL:
                    return 'enum'
            if ct.kind != T.UNEXPOSED:
                if ct.kind in (T.RECORD, T.ENUM):
                    return self.classify(ct)
                # a typedef named through a using-declaration ('using common::MyInt;'):
                # sugar over a non-record type whose written name differs from the canonical one
                ws = t.spelling
                if ws != cs and re.match(r'^(?:const |volatile )*[A-Za-z_][\w:]*$', ws):
                    return 'typedef'
                return self.classify(ct)
            if '<' in cs:
                return 'template_spec'
            return 'dependent'
        return k.name.lower()

    def type_info(self, r, t, prefix=''):
        """type / canonical_type / type_kind / canonical_kind (+ pointee and array details)."""
        T = self.T
        ct = t.get_canonical()
        r[prefix + 'type'] = strip_root(t.spelling)
        r[prefix + 'canonical_type'] = strip_root(ct.spelling)
        r[prefix + 'type_kind'] = self.classify(t)
        ck = self.classify(ct)
        r[prefix + 'canonical_kind'] = ck
        if ck in ('pointer', 'lvalue_ref', 'rvalue_ref', 'function_pointer', 'block_pointer'):
            p = ct.get_pointee()
            pu = self.unqualified(p)
            pk = self.classify(pu)
            if pk == 'template_param':
                # dependent pointee: keep the name as written ('T'), not 'type-parameter-0-0'
                wt = t
                while wt.kind == T.ELABORATED:
                    wt = wt.get_named_type()
                wp = wt.get_pointee()
                name = strip_root(self.unqualified(wp).spelling) if wp.kind != T.INVALID else strip_root(pu.spelling)
            else:
                name = strip_root(pu.spelling)
            r[prefix + 'pointee_qname'] = name
            r[prefix + 'pointee_kind'] = pk
            if p.is_const_qualified():
                r[prefix + 'pointee_const'] = 1
        elif ck in ('constant_array', 'incomplete_array', 'array'):
            et = ct.get_array_element_type()
            r[prefix + 'element_type'] = strip_root(et.spelling)
            r[prefix + 'element_kind'] = self.classify(et)
            if ck == 'constant_array':
                n = ct.get_array_size()
                if n is not None and n >= 0:
                    r[prefix + 'array_len'] = n
            if et.is_const_qualified():
                r[prefix + 'is_const'] = 1
        if ct.is_const_qualified():
            r[prefix + 'is_const'] = 1
        return ck

    # ---- records
    def add(self, r):
        key = (r['usr'], r['kind'], r['file'], r['line'])
        old = self.recs.get(key)
        if old is None:
            self.recs[key] = r
        else:
            self.dups += 1
            if dumps(old) != dumps(r):
                self.dup_conflicts += 1

    def base(self, c, kind, usr, w, name=None):
        rpath, gen, line, col = w[:4]
        r = {'kind': kind, 'usr': usr, 'file': rpath, 'line': line, 'col': col}
        nm = strip_root(c.spelling) if name is None else name
        r['name'] = nm
        pq, pusr, prec = self.scope(c.semantic_parent)
        comp = self.component(c) if name is None else name
        r['qname'] = pq + '::' + comp if pq else comp
        if pusr:
            r['parent_usr'] = pusr
            r['parent_qname'] = pq
        acc = self.ACCESS.get(c.access_specifier) if prec else None
        r['access'] = acc if acc else 'none'
        r['generated'] = 1 if gen else 0
        if kind != 'friend':
            mac = self.macro_name(w, nm, kind)
            if mac:
                r['in_macro'] = 1
                r['macro'] = mac
        return r

    def visit_children(self, c):
        for ch in c.get_children():
            self.visit(ch)

    def visit(self, c):
        K = self.K
        try:
            k = c.kind
        except ValueError:
            return None
        if k in self.CONTAINERS:
            if self.where(c) is not None:
                self.visit_children(c)
            return None
        if k in self.REC_KIND:
            w = self.where(c)
            return self.do_record(c, w) if w else None
        if k in self.FUNC_KIND:
            w = self.where(c)
            return self.do_func(c, w) if w else None
        if k == K.FIELD_DECL:
            return None                      # handled by do_record (needs the field index)
        if k == K.VAR_DECL:
            w = self.where(c)
            return self.do_var(c, w) if w else None
        if k == K.ENUM_DECL:
            w = self.where(c)
            return self.do_enum(c, w) if w else None
        if k in (K.TYPEDEF_DECL, K.TYPE_ALIAS_DECL, K.TYPE_ALIAS_TEMPLATE_DECL):
            w = self.where(c)
            return self.do_typedef(c, w) if w else None
        if k == K.FRIEND_DECL:
            w = self.where(c)
            return self.do_friend(c, w) if w else None
        return None

    def tparam(self, ch):
        d = {'name': ch.spelling, 'kind': self.TPARAM_KIND[ch.kind]}
        if ch.kind == self.K.TEMPLATE_NON_TYPE_PARAMETER:
            d['type'] = strip_root(ch.type.spelling)
        return d

    def attr_token(self, a):
        """Token at the attribute's expansion location: 'weak' for __attribute__((weak)),
        the macro name (OB_WEAK_SYMBOL) when the attribute comes from a macro."""
        w = self.where(a)
        if w is None:
            return None
        return self.token_at(w[0], w[2], w[3])

    def attr_first_token(self, a):
        for t in a.get_tokens():
            return t.spelling
        return None

    def pragma_pack_regions(self, rpath):
        regs = self.pack_cache.get(rpath)
        if regs is not None:
            return regs
        regs = []
        try:
            with open(os.path.join(ROOT, rpath), encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            text = ''
        if 'pack' in text:
            stack = []
            cur = None                     # (start_line, N)
            for i, line in enumerate(text.split('\n'), 1):
                m = PRAGMA_PACK_RE.match(line)
                if not m:
                    continue
                arg = [x.strip() for x in m.group(1).split(',') if x.strip()]
                if cur is not None:
                    regs.append((cur[0], i, cur[1]))
                    cur = None
                if not arg:                       # pack()  -> default
                    stack = []
                elif arg[0] == 'push':
                    stack.append(cur)
                    if len(arg) > 1 and arg[-1].isdigit():
                        cur = (i, int(arg[-1]))
                elif arg[0] == 'pop':
                    prev = stack.pop() if stack else None
                    if prev is not None:
                        cur = (i, prev[1])
                elif arg[0].isdigit():
                    cur = (i, int(arg[0]))
            if cur is not None:
                regs.append((cur[0], 10 ** 9, cur[1]))
        self.pack_cache[rpath] = regs
        return regs

    @staticmethod
    def valid(cur):
        """True for a real declaration cursor (not None / NoDeclFound / InvalidFile)."""
        return cur is not None and not cur.kind.is_invalid()

    def type_decl(self, t):
        """Declaration of a (base) type: canonical first, then as written."""
        d = t.get_canonical().get_declaration()
        if not self.valid(d):
            d = t.get_declaration()
        return d if self.valid(d) else None

    def base_spec(self, b):
        K = self.K
        bt = b.type
        ct = bt.get_canonical()
        d = {'access': self.ACCESS.get(b.access_specifier, 'none'),
             'virtual': 1 if self.lib.clang_isVirtualBase(b) else 0}
        decl = self.type_decl(bt)
        if decl is None:
            d['qname'] = strip_root(bt.spelling)
            d['usr'] = ''
            d['dependent'] = 1
            return d, None
        dk = decl.kind
        cs = strip_root(ct.spelling)
        if 'type-parameter-' in cs:
            d['qname'] = strip_root(bt.spelling)
            d['dependent'] = 1
        else:
            d['qname'] = cs
        d['usr'] = self.rec_usr(decl) if dk in self.REC_KIND else decl.get_usr()
        if dk in (K.STRUCT_DECL, K.CLASS_DECL, K.UNION_DECL) and decl.get_num_template_arguments() > 0:
            # implicit instantiation such as ObSEArray<int64_t, 4>: link to the primary template
            tmpl = self.lib.clang_getSpecializedCursorTemplate(decl)
            if self.valid(tmpl):
                d['template_usr'] = tmpl.get_usr()
        elif dk == K.CLASS_TEMPLATE:
            d['template_usr'] = d['usr']
            d['dependent'] = 1
        return d, decl

    def class_has_vtable(self, d):
        """Any virtual method in the record or (transitively) in a base, or a virtual base."""
        K = self.K
        u = d.get_usr()
        v = self.vt_cache.get(u)
        if v is not None:
            return v
        self.vt_cache[u] = False                 # cycle guard
        dd = d.get_definition()
        if not self.valid(dd):
            dd = d
        v = False
        for ch in dd.get_children():
            ck = ch.kind
            if ck in (K.CXX_METHOD, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
                if ch.is_virtual_method():
                    v = True
                    break
            elif ck == K.CXX_BASE_SPECIFIER:
                if self.lib.clang_isVirtualBase(ch):
                    v = True
                    break
                bd = self.type_decl(ch.type)
                if bd is not None and self.class_has_vtable(bd):
                    v = True
                    break
        self.vt_cache[u] = v
        return v

    def do_record(self, c, w):
        K = self.K
        k = c.kind
        usr = self.rec_usr(c)
        anonymous = bool(self.lib.clang_Cursor_isAnonymous(c)) if k in self.TAG else False
        r = self.base(c, self.REC_KIND[k], usr, w, name='' if anonymous else None)
        if anonymous:
            r['qname'] = (r.get('parent_qname') + '::' if r.get('parent_qname') else '') + strip_root(c.spelling)
            r['is_anonymous'] = 1
            if self.lib.clang_Cursor_isAnonymousRecordDecl(c):
                r['is_anonymous_member'] = 1
        is_def = c.is_definition()
        r['is_definition'] = 1 if is_def else 0
        if k in (K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
            r['is_template'] = 1
            tk = self.lib.clang_getTemplateCursorKind(c)
            try:
                r['tag'] = self.TAG.get(K.from_id(tk), 'class')
            except ValueError:
                pass
        if k in (K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION, K.STRUCT_DECL, K.CLASS_DECL, K.UNION_DECL):
            if k == K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION or c.get_num_template_arguments() > 0:
                st = self.lib.clang_getSpecializedCursorTemplate(c)
                if st is not None and not st.kind.is_invalid():
                    r['specialization_of'] = st.get_usr()
                # (only for hand-written declarations: the text of a macro-generated one is
                # the macro invocation, which has no braces whatever the expansion contains)
                if k != K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION and is_def and 'in_macro' not in r \
                        and self.has_brace_body(c, w) is False:
                    # 'template class X<int>;' / 'extern template ...': an explicit instantiation,
                    # not a specialization; libclang reports it as a definition without members
                    r['explicit_instantiation'] = 1
                    self.add(r)
                    return r
        if not is_def:
            self.add(r)
            return r

        bases = []
        base_decls = []
        tparams = []
        natural = 1
        own_virtual = False
        own_pure = False
        packed_attr = False
        implicit_attr = False
        field_index = 0
        last_field = None
        fields_seen = []
        last_member = None
        has_bitfield = False
        for ch in c.get_children():
            try:
                ck = ch.kind
            except ValueError:
                continue
            if ck == K.CXX_BASE_SPECIFIER:
                d, decl = self.base_spec(ch)
                bases.append(d)
                if decl is not None:
                    base_decls.append(decl)
                    a = ch.type.get_canonical().get_align()
                    if a > natural:
                        natural = a
                continue
            if ck.is_attribute():
                if ck == K.PACKED_ATTR:
                    packed_attr = True
                elif ck == K.CXX_FINAL_ATTR:
                    r['is_final'] = 1
                elif ck == K.ALIGNED_ATTR:
                    r['aligned_attr'] = 1
                elif ck == K.UNEXPOSED_ATTR and self.attr_first_token(ch) is None:
                    implicit_attr = True
                continue
            if ck in self.TPARAM_KIND:
                tparams.append(self.tparam(ch))
                continue
            if ck == K.FIELD_DECL:
                w2 = self.where(ch)
                fr, fpacked = self.do_field(ch, w2, field_index) if w2 else (None, False)
                if w2 is None:
                    fpacked = any(g.kind == K.PACKED_ATTR for g in ch.get_children())
                field_index += 1
                fct = ch.type.get_canonical()
                if fpacked:
                    a = 1
                elif fct.kind in (self.T.LVALUEREFERENCE, self.T.RVALUEREFERENCE):
                    a = 8                     # stored as a pointer (LP64); alignof(T&) is alignof(T)
                else:
                    a = fct.get_align()
                if a > natural:
                    natural = a
                if ch.is_bitfield():
                    has_bitfield = True
                last_field = (ch, fr)
                fields_seen.append((ch, fr))
                last_member = fr
                continue
            if ck in (K.CXX_METHOD, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
                if ch.is_virtual_method():
                    own_virtual = True
                    if ch.is_pure_virtual_method():
                        own_pure = True
                self.visit(ch)
                continue
            if ck in self.TAG:
                anon_member = bool(self.lib.clang_Cursor_isAnonymousRecordDecl(ch))
                sub = self.visit(ch)
                if anon_member:
                    # 'union { ... };' inside a record: an unnamed data member (clang's implicit
                    # field is not visited), numbered together with the named fields
                    a = ch.type.get_align()
                    if a > natural:
                        natural = a
                    if sub is not None:
                        sub['field_index'] = field_index
                    field_index += 1
                    last_member = sub
                continue
            self.visit(ch)

        if bases:
            r['bases'] = bases
        if tparams:
            r['template_params'] = tparams
        t = c.type
        size = t.get_size() if k not in (K.CLASS_TEMPLATE,) else -1
        align = t.get_align() if k not in (K.CLASS_TEMPLATE,) else -1
        if size is not None and size >= 0:
            r['size'] = size
        if align is not None and align > 0:
            r['align'] = align
        if k in (K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
            if own_pure:
                r['is_abstract'] = 1
        elif c.is_abstract_record():
            r['is_abstract'] = 1
        hv = own_virtual or any(b.get('virtual') for b in bases)
        if not hv:
            for bd in base_decls:
                if self.class_has_vtable(bd):
                    hv = True
                    break
        if hv:
            r['has_vtable'] = 1
        if any(b.get('virtual') for b in bases):
            r['has_virtual_base'] = 1
        if any(b.get('dependent') for b in bases):
            r['has_dependent_base'] = 1
        if has_bitfield:
            r['has_bitfield'] = 1
        if k not in (K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION) and size is not None and size >= 0:
            if t.is_pod():
                r['is_pod'] = 1
        # flexible array member: the last field is T[] or T[0]; in a union every such
        # member overlays the trailing data; a record whose last data member is an
        # anonymous struct/union ending in one has trailing data too
        for fch, ffr in (fields_seen if k == K.UNION_DECL else fields_seen[-1:]):
            fct = fch.type.get_canonical()
            if fct.kind == self.T.INCOMPLETEARRAY or (
                    fct.kind == self.T.CONSTANTARRAY and fct.get_array_size() == 0):
                r['has_flexible_array'] = 1
                if ffr is not None:
                    ffr['is_flexible_array'] = 1
        if last_member is not None and last_member.get('is_anonymous_member') \
                and last_member.get('has_flexible_array'):
            r['has_flexible_array'] = 1
        # packing
        regs = self.pragma_pack_regions(w[0])
        pp = None
        for (s, e, n) in regs:
            if s < w[2] < e:
                pp = n
        if pp is not None:
            r['pragma_pack'] = pp
        if packed_attr:
            r['packed'] = 1
            r['packed_by'] = 'attribute'
        elif align is not None and 0 < align < natural:
            r['packed'] = 1
            r['packed_by'] = 'pragma' if (implicit_attr or pp is not None) else 'layout'
        elif implicit_attr and pp is not None and pp < natural and k in (
                K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
            r['packed'] = 1
            r['packed_by'] = 'pragma'
        if field_index:
            r['n_fields'] = field_index
        el = self.end_line(c, w)
        if el is not None:
            r['end_line'] = el
        self.add(r)
        return r

    def do_field(self, c, w, idx):
        K = self.K
        r = self.base(c, 'field', c.get_usr(), w)
        self.type_info(r, c.type)
        r['field_index'] = idx
        if c.is_mutable_field():
            r['is_mutable'] = 1
        if c.is_bitfield():
            r['bitwidth'] = c.get_bitfield_width()
        off = c.get_field_offsetof()
        if off is not None and off >= 0:
            r['offset_bits'] = off
        if 'in_macro' not in r:
            # default member initializer: '= ...' or '{...}' after the declarator
            text = self.decl_text(c, w)
            if text is not None and re.search(rb'[={]', text):
                r['has_init'] = 1
        fpacked = False
        for g in c.get_children():
            gk = g.kind
            if gk == K.PACKED_ATTR:
                fpacked = True
                r['packed'] = 1
            elif gk == K.ALIGNED_ATTR:
                r['aligned_attr'] = 1
        self.add(r)
        return r, fpacked

    def param(self, p):
        d = {'name': p.spelling}
        t = p.type
        ct = t.get_canonical()
        d['type'] = strip_root(t.spelling)
        d['canonical_type'] = strip_root(ct.spelling)
        d['kind'] = self.classify(ct)
        if d['kind'] in ('pointer', 'lvalue_ref', 'rvalue_ref'):
            d['pointee_kind'] = self.classify(self.unqualified(ct.get_pointee()))
            if ct.get_pointee().is_const_qualified():
                d['pointee_const'] = 1
        for g in p.get_children():
            if g.kind.is_expression():
                d['has_default'] = 1
                break
        return d

    def do_func(self, c, w):
        K = self.K
        k = c.kind
        kind = self.FUNC_KIND[k]
        r = self.base(c, kind, c.get_usr(), w)
        method_like = kind in ('method', 'ctor', 'dtor', 'conversion')
        if k == K.FUNCTION_TEMPLATE:
            r['is_template'] = 1
            try:
                tk = K.from_id(self.lib.clang_getTemplateCursorKind(c))
                tkind = self.FUNC_KIND.get(tk, tk.name.lower())
            except ValueError:
                tkind = 'function'
            r['template_kind'] = tkind
            method_like = tkind in ('method', 'ctor', 'dtor', 'conversion')
        is_def = c.is_definition()
        r['is_definition'] = 1 if is_def else 0
        r['sig'] = strip_root(c.displayname)
        el = self.end_line(c, w)
        if el is not None:
            r['end_line'] = el
        params = []
        tparams = []
        weak = False
        for ch in c.get_children():
            try:
                ck = ch.kind
            except ValueError:
                continue
            if ck == K.PARM_DECL:
                params.append(self.param(ch))
            elif ck in self.TPARAM_KIND:
                tparams.append(self.tparam(ch))
            elif ck == K.CXX_OVERRIDE_ATTR:
                r['override_kw'] = 1
            elif ck == K.CXX_FINAL_ATTR:
                r['is_final'] = 1
            elif ck == K.UNEXPOSED_ATTR:
                if self.attr_token(ch) in WEAK_TOKENS:
                    weak = True
        r['params'] = params
        if tparams:
            r['template_params'] = tparams
        if kind not in ('ctor', 'dtor') and not (k == K.FUNCTION_TEMPLATE and r.get('template_kind') in ('ctor', 'dtor')):
            rt = c.result_type
            if rt.kind != self.T.INVALID:
                r['return_type'] = strip_root(rt.spelling)
                rct = rt.get_canonical()
                r['canonical_return_type'] = strip_root(rct.spelling)
                r['return_kind'] = self.classify(rct)
        if method_like:
            if c.is_const_method():
                r['is_const'] = 1
            if c.is_static_method():
                r['is_static'] = 1
            if c.is_virtual_method():
                r['is_virtual'] = 1
                ov = self.overridden(c)
                if ov:
                    r['is_override'] = 1
                    r['overrides'] = ov
            if c.is_pure_virtual_method():
                r['is_pure'] = 1
            if c.is_deleted_method():
                r['is_deleted'] = 1
            if c.is_default_method():
                r['is_defaulted'] = 1
            if kind in ('ctor', 'conversion') or r.get('template_kind') in ('ctor', 'conversion'):
                if c.is_explicit_method():
                    r['is_explicit'] = 1
            if kind == 'ctor':
                if c.is_copy_constructor():
                    r['is_copy_ctor'] = 1
                if c.is_move_constructor():
                    r['is_move_ctor'] = 1
                if c.is_default_constructor():
                    r['is_default_ctor'] = 1
                if c.is_converting_constructor():
                    r['is_converting_ctor'] = 1
            if kind == 'method':
                if c.is_copy_assignment_operator_method():
                    r['is_copy_assign'] = 1
                if c.is_move_assignment_operator_method():
                    r['is_move_assign'] = 1
                rq = c.type.get_ref_qualifier()
                if rq == self.ci.RefQualifierKind.LVALUE:
                    r['ref_qual'] = '&'
                elif rq == self.ci.RefQualifierKind.RVALUE:
                    r['ref_qual'] = '&&'
        sc = self.STORAGE.get(c.storage_class)
        if sc:
            r['storage'] = sc
            if sc == 'static' and not method_like:
                r['is_static'] = 1
        if self.lib.clang_Cursor_isFunctionInlined(c):
            r['is_inline'] = 1
        if self.lib.clang_Cursor_isVariadic(c):
            r['is_variadic'] = 1
        lk = self.LINKAGE.get(c.linkage)
        if lk:
            r['linkage'] = lk
        if r.get('is_defaulted'):
            r['is_definition'] = 1
        es = None
        if kind != 'dtor' and not r.get('is_defaulted'):
            try:
                es = self.EXC_SPEC.get(c.exception_specification_kind)
            except ValueError:
                es = None
        if es:
            r['exception_spec'] = es
        if weak:
            r['weak'] = 1
        # out-of-line: defined at namespace scope but belonging to a class or another namespace
        # (a friend function defined inside its class is not out-of-line)
        sp = c.semantic_parent
        lp = c.lexical_parent
        if sp is not None and lp is not None and lp.kind not in self.REC_KIND and sp != lp:
            r['out_of_line'] = 1
        if kind != 'function_template' and c.get_num_template_arguments() > 0:
            st = self.lib.clang_getSpecializedCursorTemplate(c)
            if st is not None and not st.kind.is_invalid():
                r['specialization_of'] = st.get_usr()
            r['template_args'] = self.template_args_text(c)
            if is_def and not r.get('is_deleted') and not r.get('is_defaulted') and 'in_macro' not in r \
                    and self.has_brace_body(c, w) is False:
                r['explicit_instantiation'] = 1
        self.add(r)
        return r

    def do_var(self, c, w):
        K = self.K
        r = self.base(c, 'var', c.get_usr(), w)
        r['is_definition'] = 1 if c.is_definition() else 0
        self.type_info(r, c.type)
        sc = self.STORAGE.get(c.storage_class)
        if sc:
            r['storage'] = sc
            if sc == 'static':
                r['is_static'] = 1
        p = c.semantic_parent
        if p is not None and p.kind in self.REC_KIND:
            r['is_static'] = 1
            r['is_member'] = 1
        tls = self.TLS.get(c.tls_kind)
        if tls:
            r['is_thread_local'] = 1
            r['tls'] = tls
        lk = self.LINKAGE.get(c.linkage)
        if lk:
            r['linkage'] = lk
        for ch in c.get_children():
            try:
                ck = ch.kind
            except ValueError:
                continue
            if ck == K.UNEXPOSED_ATTR:
                if self.attr_token(ch) in WEAK_TOKENS:
                    r['weak'] = 1
            elif ck.is_expression() and 'has_init' not in r:
                r['has_init'] = 1
                r['init_kind'] = ck.name.lower()
        self.add(r)
        return r

    def do_enum(self, c, w):
        K = self.K
        usr = self.rec_usr(c)
        anonymous = not c.spelling or bool(self.lib.clang_Cursor_isAnonymous(c))
        r = self.base(c, 'enum', usr, w, name='' if anonymous else None)
        if anonymous:
            r['is_anonymous'] = 1
            r['qname'] = (r.get('parent_qname') + '::' if r.get('parent_qname') else '') + strip_root(c.spelling)
        is_def = c.is_definition()
        r['is_definition'] = 1 if is_def else 0
        try:
            ut = c.enum_type
            r['underlying_type'] = strip_root(ut.spelling)
            r['canonical_underlying_type'] = strip_root(ut.get_canonical().spelling)
        except Exception:
            pass
        if c.is_scoped_enum():
            r['is_scoped'] = 1
        sz = c.type.get_size()
        if sz is not None and sz > 0:
            r['size'] = sz
        n = 0
        if is_def:
            for ch in c.get_children():
                if ch.kind != K.ENUM_CONSTANT_DECL:
                    continue
                n += 1
                w2 = self.where(ch)
                if w2 is None:
                    continue
                er = self.base(ch, 'enum_constant', ch.get_usr(), w2)
                er['qname'] = (r['qname'] + '::' + ch.spelling) if r['qname'] else ch.spelling
                er['parent_usr'] = usr
                er['parent_qname'] = r['qname']
                try:
                    er['value'] = ch.enum_value
                except Exception:
                    pass
                er['index'] = n - 1
                self.add(er)
            r['n_constants'] = n
            el = self.end_line(c, w)
            if el is not None:
                r['end_line'] = el
        self.add(r)
        return r

    def do_typedef(self, c, w):
        K = self.K
        k = c.kind
        r = self.base(c, 'typedef' if k == K.TYPEDEF_DECL else 'alias', c.get_usr(), w)
        if k == K.TYPE_ALIAS_TEMPLATE_DECL:
            r['is_template'] = 1
            tparams = []
            inner = None
            for ch in c.get_children():
                if ch.kind in self.TPARAM_KIND:
                    tparams.append(self.tparam(ch))
                elif ch.kind == K.TYPE_ALIAS_DECL:
                    inner = ch
            if tparams:
                r['template_params'] = tparams
            if inner is None:
                self.add(r)
                return r
            ut = inner.underlying_typedef_type
        else:
            ut = c.underlying_typedef_type
        self.type_info(r, ut, prefix='underlying_')
        # keep the spec's names: underlying_type, canonical_type, type_kind
        r['canonical_type'] = r.pop('underlying_canonical_type')
        r['type_kind'] = r.pop('underlying_type_kind')
        r['canonical_kind'] = r.pop('underlying_canonical_kind')
        for key in ('pointee_qname', 'pointee_kind', 'pointee_const', 'element_type', 'element_kind',
                    'array_len', 'is_const'):
            if 'underlying_' + key in r:
                r[key] = r.pop('underlying_' + key)
        self.add(r)
        return r

    def do_friend(self, c, w):
        K = self.K
        target = None
        tdecl = None
        for ch in c.get_children():
            ck = ch.kind
            if ck in self.FUNC_KIND or ck in self.REC_KIND:
                target = ch
                tdecl = ch
                break
            if ck in (K.TYPE_REF, K.TEMPLATE_REF):
                ref = ch.referenced
                if ref is not None and not ref.kind.is_invalid():
                    target = ref
                else:
                    d = ch.type.get_declaration()
                    if d is not None and not d.kind.is_invalid() and d.kind != K.NO_DECL_FOUND:
                        target = d
                if target is not None:
                    break
        if target is None:
            return None
        if target.kind in self.REC_KIND:
            dfn = target.get_definition()
            if self.valid(dfn):
                target = dfn
        tk = target.kind
        tkind = self.REC_KIND.get(tk) or self.FUNC_KIND.get(tk) or tk.name.lower()
        tusr = self.rec_usr(target) if tk in self.REC_KIND else target.get_usr()
        tq = self.scope(target.semantic_parent)[0]
        tcomp = self.component(target)
        r = self.base(c, 'friend', tusr, w, name=strip_root(target.spelling))
        pq = self.scope(c.semantic_parent)[0]
        r['qname'] = (pq + '::' if pq else '') + 'friend ' + (tq + '::' + tcomp if tq else tcomp)
        r['friend_kind'] = tkind
        r['friend_qname'] = tq + '::' + tcomp if tq else tcomp
        r['access'] = 'none'
        self.add(r)
        if tdecl is not None and tk in self.FUNC_KIND and tdecl.is_definition():
            w2 = self.where(tdecl)
            if w2 is not None:
                fr = self.do_func(tdecl, w2)
                fr['is_friend'] = 1
        return r

    def run(self):
        for c in self.tu.cursor.get_children():
            self.visit(c)
        return self.recs


def record_sort_key(r):
    return (r['file'], r['line'], r.get('col', 0), r['kind'], r['usr'])


def parse_one(name, force=False):
    """Parse one TU and write its shard and meta (meta last = complete)."""
    import clang.cindex as ci
    fc = FileClasses()
    ents = {e['name']: e for e in load_entries(fc)}
    ent = ents.get(name)
    if ent is None:
        raise SystemExit('unknown TU %s' % name)
    os.makedirs(TU_DIR, exist_ok=True)
    args = libclang_args(ent)
    meta = {'schema': SCHEMA, 'extractor_sha': extractor_digest(), 'tu': name,
            'file': ent['rel'] or ent['file'], 'lang': ent['lang'],
            'args_sha': args_digest(args), 'libclang': ci.conf.lib._name if hasattr(ci.conf.lib, '_name') else ''}
    shard = os.path.join(TU_DIR, name + '.jsonl')
    meta_path = os.path.join(TU_DIR, name + '.meta.json')
    t0 = time.time()
    index = ci.Index.create()
    tu = index.parse(ent['file'], args=args, options=PARSE_OPTIONS)
    t1 = time.time()
    sev = {'ignored': 0, 'note': 0, 'warning': 0, 'error': 0, 'fatal': 0}
    names = {0: 'ignored', 1: 'note', 2: 'warning', 3: 'error', 4: 'fatal'}
    first = {}
    for d in tu.diagnostics:
        s = names.get(d.severity, 'fatal')
        sev[s] += 1
        if s in ('error', 'fatal') and s not in first:
            loc = d.location
            f = rel(loc.file.name) if loc.file is not None else None
            if loc.file is not None and f is None:
                f = loc.file.name
            first[s] = '%s:%s: %s' % (f or '?', loc.line, strip_root(d.spelling))
    ext = Extractor(ci, tu, fc)
    recs = ext.run()
    seen = set()
    for inc in tu.get_includes():
        r = rel(inc.include.name) if inc.include is not None else None
        if r is not None and fc.classify(r) is not None:
            seen.add(r)
    if ent['rel'] and fc.classify(ent['rel']) is not None:
        seen.add(ent['rel'])
    t2 = time.time()
    lines = [dumps(r) for r in sorted(recs.values(), key=lambda r: (record_sort_key(r), dumps(r)))]
    write_atomic(shard, ''.join(l + '\n' for l in lines))
    meta.update({'parse_s': round(t1 - t0, 2), 'extract_s': round(t2 - t1, 2), 'diag': sev,
                 'first_fatal': first.get('fatal', ''), 'first_error': first.get('error', ''),
                 'n_records': len(lines), 'in_tu_duplicates': ext.dups,
                 'in_tu_duplicate_conflicts': ext.dup_conflicts,
                 'untracked_src_files': sorted(ext.untracked),
                 'files_seen': sorted(seen),
                 'status': 'fatal' if sev['fatal'] else ('error' if sev['error'] else 'ok')})
    write_atomic(meta_path, json.dumps(meta, sort_keys=True, indent=1) + '\n')
    return meta


# --------------------------------------------------------------------------- driver

class RunLog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, msg):
        line = '[%s] %s' % (now(), msg)
        with self.lock:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
            print(line, flush=True)


def shard_is_current(ent):
    meta_path = os.path.join(TU_DIR, ent['name'] + '.meta.json')
    shard = os.path.join(TU_DIR, ent['name'] + '.jsonl')
    if not (os.path.exists(meta_path) and os.path.exists(shard)):
        return False
    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return False
    return (meta.get('schema') == SCHEMA and meta.get('extractor_sha') == extractor_digest()
            and meta.get('args_sha') == args_digest(libclang_args(ent)))


def tu_cost(ent):
    """Rough cost estimate for scheduling (largest first): size of the .o.d dependency file."""
    out = ent.get('output') or ''
    dep = os.path.join(BUILD, out + '.d') if out else ''   # 'output' is relative to the build dir
    try:
        return os.path.getsize(dep)
    except OSError:
        return 0


def cmd_run(opts):
    os.makedirs(TU_DIR, exist_ok=True)
    log = RunLog(os.path.join(OUT, 'run.log'))
    ents = load_entries()
    todo = []
    skipped = 0
    for e in ents:
        if opts.only and not any(s in e['name'] for s in opts.only):
            continue
        if e['skip']:
            skipped += 1
            continue
        if not opts.force and shard_is_current(e):
            continue
        todo.append(e)
    todo.sort(key=lambda e: (-tu_cost(e), e['name']))
    log('run start: %d TUs in compile_commands, %d skipped (third-party/unbuilt main file), %d to parse, jobs=%d'
        % (len(ents), skipped, len(todo), opts.jobs))
    t0 = time.time()
    done = [0]
    failed = []
    lock = threading.Lock()

    def work(e):
        logf = os.path.join(TU_DIR, e['name'] + '.log')
        meta_path = os.path.join(TU_DIR, e['name'] + '.meta.json')
        for stale in (meta_path, os.path.join(TU_DIR, e['name'] + '.jsonl')):
            if os.path.exists(stale):
                os.remove(stale)          # a crashed re-parse must not leave an old shard behind
        st = time.time()
        with open(logf, 'w', encoding='utf-8') as lf:
            try:
                p = subprocess.run([sys.executable, '-I', os.path.abspath(__file__), 'parse-one', '--tu', e['name']],
                                   stdout=lf, stderr=subprocess.STDOUT, timeout=TU_TIMEOUT_S, cwd=ROOT)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = 'timeout'
        el = time.time() - st
        msg = ''
        if rc == 0 and os.path.exists(meta_path):
            with open(meta_path, encoding='utf-8') as f:
                m = json.load(f)
            msg = 'status=%s parse=%.1fs extract=%.1fs records=%d fatal=%d error=%d' % (
                m['status'], m['parse_s'], m['extract_s'], m['n_records'], m['diag']['fatal'], m['diag']['error'])
        else:
            failed.append(e['name'])
            msg = 'FAILED rc=%s (see tu/%s.log)' % (rc, e['name'])
        with lock:
            done[0] += 1
            log('%d/%d %s %.1fs %s' % (done[0], len(todo), e['name'], el, msg))

    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as ex:
        list(ex.map(work, todo))
    log('parse phase done in %.1f min, %d failed%s' % ((time.time() - t0) / 60.0, len(failed),
                                                       (': ' + ' '.join(failed)) if failed else ''))
    if not opts.no_merge:
        cmd_merge(opts, log)
        cmd_stats(opts, log)


# --------------------------------------------------------------------------- merge

FUNC_KINDS = ('function', 'method', 'ctor', 'dtor', 'conversion', 'function_template')
REC_KINDS = ('class', 'struct', 'union', 'class_template', 'partial_specialization')


def cmd_merge(opts, log=None):
    log = log or RunLog(os.path.join(OUT, 'run.log'))
    t0 = time.time()
    ents = load_entries()
    metas = {}
    for e in ents:
        mp = os.path.join(TU_DIR, e['name'] + '.meta.json')
        if os.path.exists(mp):
            with open(mp, encoding='utf-8') as f:
                metas[e['name']] = json.load(f)
    names = sorted(n for n in metas if os.path.exists(os.path.join(TU_DIR, n + '.jsonl')))
    seen = {}          # digest of a shard line -> key
    variants = {}      # key -> {line: [n_tu, is_cxx]}
    total_lines = 0
    for n in names:
        is_cxx = metas[n].get('lang') == 'c++'
        with open(os.path.join(TU_DIR, n + '.jsonl'), 'rb') as f:
            for raw in f:
                total_lines += 1
                dg = hashlib.blake2b(raw, digest_size=16).digest()
                key = seen.get(dg)
                if key is None:
                    rec = json.loads(raw)
                    key = (rec['usr'], rec['kind'], rec['file'], rec['line'])
                    seen[dg] = key
                    vs = variants.get(key)
                    if vs is None:
                        vs = variants[key] = {}
                    vs[raw] = [0, False]
                v = variants[key][raw]
                v[0] += 1
                if is_cxx:
                    v[1] = True
    del seen
    recs = []
    conflicts = 0
    for key, vs in variants.items():
        # prefer the C++ parse, then the variant seen in most TUs, then the smallest text
        best = sorted(vs.items(), key=lambda kv: (not kv[1][1], -kv[1][0], kv[0]))[0][0]
        r = json.loads(best)
        r['n_tu'] = sum(v[0] for v in vs.values())
        if len(vs) > 1:
            r['variants'] = len(vs)
            conflicts += 1
        recs.append(r)
    del variants

    # link declarations and definitions by USR
    by_usr = {}
    for r in recs:
        k = r['kind']
        if k in FUNC_KINDS:
            fam = 'f:' + k
        elif k in REC_KINDS:
            fam = 'r'
        elif k in ('var', 'enum'):
            fam = k
        else:
            continue
        by_usr.setdefault((fam, r['usr']), []).append(r)

    def pick(cands, r):
        return sorted(cands, key=lambda d: (d['file'] != r['file'], d['file'], d['line'], d.get('col', 0)))[0]

    for (fam, usr), group in by_usr.items():
        if not usr:
            continue
        defs = [d for d in group if d.get('is_definition') and not d.get('explicit_instantiation')]
        decls = [d for d in group if not d.get('is_definition')]
        if fam.startswith('f:'):
            bodies = [d for d in defs if not d.get('is_deleted') and not d.get('explicit_instantiation')]
            for r in group:
                if bodies:
                    r['has_body'] = 1
                if len(defs) > 1:
                    r['n_defs'] = len(defs)
                if not r.get('is_definition') and bodies:
                    d = pick(bodies, r)
                    r['body_file'] = d['file']
                    r['body_line'] = d['line']
                if r.get('is_definition') and r.get('out_of_line') and decls:
                    inclass = [d for d in decls if not d.get('out_of_line')] or decls
                    d = pick(inclass, r)
                    r['decl_file'] = d['file']
                    r['decl_line'] = d['line']
        else:
            if not defs:
                continue
            for r in group:
                if len(defs) > 1:
                    r['n_defs'] = len(defs)
                if not r.get('is_definition'):
                    d = pick(defs, r)
                    r['def_file'] = d['file']
                    r['def_line'] = d['line']

    recs.sort(key=lambda r: (record_sort_key(r), dumps(r)))
    out = os.path.join(OUT, 'decls.jsonl')
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(dumps(r) + '\n')
    os.replace(tmp, out)

    # errors.tsv
    # (timings stay in the meta files and run.log so that errors.tsv is deterministic too)
    rows = ['tu\tmain_file\tlang\tstatus\tfatal\terror\twarning\tnote\trecords\tfirst_fatal\tfirst_error\tremark']
    untracked = set()

    def clean(s):
        return (s or '').replace('\t', ' ').replace('\n', ' ')

    for e in ents:
        m = metas.get(e['name'])
        if e['skip']:
            rows.append('\t'.join([e['name'], e['rel'] or e['file'], e['lang'], 'skipped'] + [''] * 7 + [e['skip']]))
            continue
        if m is None:
            rows.append('\t'.join([e['name'], e['rel'] or e['file'], e['lang'], 'missing'] + [''] * 7
                                  + ['no shard: parse failed or not run']))
            continue
        d = m['diag']
        untracked.update(m.get('untracked_src_files', []))
        rows.append('\t'.join(str(x) for x in [
            e['name'], m['file'], m['lang'], m['status'], d['fatal'], d['error'], d['warning'], d['note'],
            m['n_records'], clean(m['first_fatal']), clean(m['first_error']), '']))
    write_atomic(os.path.join(OUT, 'errors.tsv'), '\n'.join(rows) + '\n')

    # files.tsv: every first-party file of the depmap plus every generated file that was
    # parsed, with the number of TUs that include it and of records located in it
    fc = FileClasses()
    tus_per_file = {}
    for n in names:
        for fpath in metas[n].get('files_seen', []):
            tus_per_file[fpath] = tus_per_file.get(fpath, 0) + 1
    recs_per_file = {}
    for r in recs:
        recs_per_file[r['file']] = recs_per_file.get(r['file'], 0) + 1
    allf = set(fc.nodes) | set(tus_per_file) | set(recs_per_file)
    frows = ['file\tclass\ttus\trecords']
    never = 0
    for fpath in sorted(allf):
        g = fc.classify(fpath)
        cls = 'first-party' if g == 0 else ('generated' if g == 1 else 'other')
        t = tus_per_file.get(fpath, 0)
        if t == 0 and g == 0:
            never += 1
        frows.append('%s\t%s\t%d\t%d' % (fpath, cls, t, recs_per_file.get(fpath, 0)))
    write_atomic(os.path.join(OUT, 'files.tsv'), '\n'.join(frows) + '\n')
    write_atomic(os.path.join(OUT, 'merge-info.json'), json.dumps({
        'schema': SCHEMA, 'shards': len(names), 'shard_lines': total_lines, 'records': len(recs),
        'keys_with_variants': conflicts, 'untracked_src_files_seen': sorted(untracked),
        'first_party_files_never_parsed': never},
        sort_keys=True, indent=1) + '\n')
    h = hashlib.sha256()
    with open(out, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    log('merge: %d shards, %d shard lines -> %d records (%d keys with >1 variant) in %.1fs; decls.jsonl sha256=%s'
        % (len(names), total_lines, len(recs), conflicts, time.time() - t0, h.hexdigest()))


# --------------------------------------------------------------------------- stats

def cmd_stats(opts, log=None):
    """Counts for the README validation section -> summary.json."""
    log = log or RunLog(os.path.join(OUT, 'run.log'))
    from collections import Counter
    by_kind = Counter()
    by_kind_gen = Counter()
    field_tk = Counter()
    field_ck = Counter()
    c = Counter()
    out_params = {}
    weak_usrs = set()
    with open(os.path.join(OUT, 'decls.jsonl'), encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            k = r['kind']
            by_kind[k] += 1
            if r.get('generated'):
                by_kind_gen[k] += 1
            if r.get('generated'):
                continue
            if k in REC_KINDS and r.get('is_definition'):
                c['record_definitions'] += 1
                if r.get('has_vtable'):
                    c['records_with_vtable'] += 1
                if k == 'union':
                    c['union_definitions'] += 1
                if r.get('packed'):
                    c['packed_records'] += 1
                    c['packed_by_' + r.get('packed_by', '?')] += 1
                if r.get('pragma_pack'):
                    c['records_in_pragma_pack_region'] += 1
                if r.get('has_flexible_array'):
                    c['records_with_flexible_array'] += 1
                if r.get('has_virtual_base'):
                    c['records_with_virtual_base'] += 1
                    c['virtual_base_specifiers'] += sum(1 for b in r.get('bases', []) if b.get('virtual'))
                if r.get('is_abstract'):
                    c['abstract_records'] += 1
            if k == 'field':
                field_tk[r.get('type_kind')] += 1
                field_ck[r.get('canonical_kind')] += 1
                if 'bitwidth' in r:
                    c['bitfields'] += 1
                if r.get('is_flexible_array'):
                    c['flexible_array_fields'] += 1
                ck = r.get('canonical_kind')
                if ck == 'pointer':
                    c['pointer_fields'] += 1
                    if r.get('pointee_qname') == 'void':
                        c['void_pointer_fields'] += 1
                if ck == 'function_pointer':
                    c['function_pointer_fields'] += 1
                if ck in ('lvalue_ref', 'rvalue_ref'):
                    c['reference_fields'] += 1
                if r.get('canonical_type', '').replace('const ', '') == 'oceanbase::common::ObString':
                    c['obstring_fields'] += 1
                if r.get('has_init'):
                    c['fields_with_initializer'] += 1
            if k in FUNC_KINDS:
                n_out = 0
                for p in r.get('params', []):
                    if p.get('kind') == 'lvalue_ref' and p.get('pointee_kind') == 'pointer':
                        if p.get('pointee_const'):
                            c['params_T_ptr_const_ref'] += 1        # T *const &: not an out-param
                        else:
                            n_out += 1
                if n_out:
                    c['params_T_ptr_ref_all_records'] += n_out    # every declaration and definition
                    out_params[r['usr']] = n_out
                if r.get('weak'):
                    weak_usrs.add(r['usr'])
                if r.get('in_macro'):
                    c['functions_from_macros'] += 1
    c['params_T_ptr_ref_unique_functions'] = sum(out_params.values())
    c['functions_with_T_ptr_ref_param'] = len(out_params)
    c['weak_functions'] = len(weak_usrs)
    summary = {'records_by_kind': dict(sorted(by_kind.items())),
               'generated_records_by_kind': dict(sorted(by_kind_gen.items())),
               'first_party_fields_by_type_kind': dict(sorted(field_tk.items(), key=lambda kv: str(kv[0]))),
               'first_party_fields_by_canonical_kind': dict(sorted(field_ck.items(), key=lambda kv: str(kv[0]))),
               'first_party_counts': dict(sorted(c.items()))}
    write_atomic(os.path.join(OUT, 'summary.json'), json.dumps(summary, sort_keys=True, indent=1) + '\n')
    log('stats: %s' % json.dumps(summary['first_party_counts'], sort_keys=True))


# --------------------------------------------------------------------------- probe

class _ProbeClasses(FileClasses):
    """Every file under one directory (inside the repo) counts as first-party."""

    def __init__(self, prefix):
        self.nodes, self.excluded, self.prefix = set(), {}, prefix

    def classify(self, relpath):
        return 0 if relpath is not None and relpath.startswith(self.prefix) else None

    def is_untracked_src(self, relpath):
        return False


def cmd_probe(opts):
    """Debug aid: extract one standalone file (e.g. a feature test) and print its records."""
    import clang.cindex as ci
    path = os.path.abspath(opts.file)
    r = rel(path)
    if r is None:
        raise SystemExit('probe file must be inside the repository (e.g. build_release/inventory-ast/selftest/)')
    compiler = os.path.join(ROOT, 'deps/3rd/usr/local/oceanbase/devtools/bin/clang++')
    args = ['-std=gnu++20', '-isysroot', '/Library/Developer/CommandLineTools/SDKs/MacOSX15.sdk']
    rd = resource_dir_for(compiler)
    if rd:
        args += ['-resource-dir', rd]
    args += opts.clang_args or []
    tu = ci.Index.create().parse(path, args=args, options=PARSE_OPTIONS)
    for d in tu.diagnostics:
        print('# diag severity=%d %s:%s %s' % (d.severity, d.location.file, d.location.line, d.spelling))
    ext = Extractor(ci, tu, _ProbeClasses(os.path.dirname(r) + '/'))
    for rec in sorted(ext.run().values(), key=lambda x: (record_sort_key(x), dumps(x))):
        print(dumps(rec))


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('run', help='parse all first-party TUs (resumable), then merge and stats')
    p.add_argument('--jobs', type=int, default=8)
    p.add_argument('--force', action='store_true', help='re-parse even when the shard is current')
    p.add_argument('--only', action='append', help='only TUs whose name contains this substring')
    p.add_argument('--no-merge', action='store_true')
    p = sub.add_parser('parse-one', help='(internal) parse one TU and write its shard')
    p.add_argument('--tu', required=True)
    sub.add_parser('merge', help='merge shards into decls.jsonl and errors.tsv')
    sub.add_parser('stats', help='recompute summary.json from decls.jsonl')
    sub.add_parser('list', help='list TUs')
    p = sub.add_parser('args', help='print the libclang argument vector of a TU')
    p.add_argument('--tu', required=True)
    p = sub.add_parser('probe', help='(debug) extract a standalone file inside the repo and print records')
    p.add_argument('file')
    p.add_argument('clang_args', nargs='*')
    opts = ap.parse_args(argv)
    if opts.cmd == 'probe':
        cmd_probe(opts)
        return
    if opts.cmd == 'run':
        cmd_run(opts)
    elif opts.cmd == 'parse-one':
        m = parse_one(opts.tu)
        print(json.dumps(m, sort_keys=True))
    elif opts.cmd == 'merge':
        os.makedirs(OUT, exist_ok=True)
        cmd_merge(opts)
    elif opts.cmd == 'stats':
        cmd_stats(opts)
    elif opts.cmd == 'list':
        for e in load_entries():
            print('%s\t%s\t%s\t%s' % (e['name'], e['rel'] or e['file'], e['lang'], e['skip'] or ''))
    elif opts.cmd == 'args':
        ents = {e['name']: e for e in load_entries()}
        e = ents[opts.tu]
        print(shlex.join(['<libclang>', e['file']] + libclang_args(e)))


if __name__ == '__main__':
    main()
