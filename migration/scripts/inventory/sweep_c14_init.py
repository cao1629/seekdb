#!/usr/bin/env python3
"""c14-init: pre-main initialisation and object pools (RULEBOOK 0.4 main 之前的初始化, 0.5 main 的开头,
0.9 对象池, §2.13 命名空间作用域里带构造函数的全局对象).

Sites:
  A  `__attribute__((constructor[(prio)]))` and `__attribute__((init_priority(n)))` (stripped first-party text)
  B  dynamically initialised namespace-scope objects, as compiled into the original binary: the
     `__TEXT,__init_offsets` section of build_release/src/observer/seekdb gives the initialiser order;
     the binary's debug map (N_OSO stabs, matched to build_release/src/**/*.o by basename + mtime) gives
     each initialiser's object; every instruction of the object's `_GLOBAL__sub_I*` / `__cxx_global_var_init*`
     functions is symbolised with the bundled llvm-symbolizer (--inlining) and each inlined
     `__cxx_global_var_init.N` frame with a line gives one object's definition site (file:line:col)
  C  object pools: data members / globals whose type is an object pool (`*ObjPool<...>`, `ObServerObjectPool`,
     `ObPooledAllocator`, `ObPool<...>`, `ObTabletMemtableMgrPool`, `ObIOCBPool`), uses of the small-object
     pool macros (sop_borrow/sop_return/op_alloc/op_free/op_reclaim_alloc/op_reclaim_free/rp_alloc/rp_free),
     and the RULEBOOK 0.9 pool anchors (tx ctx factory limit, log callback alloc/free)
Writes migration/inventory/sweep/c14-init.tsv and c14-init-order.tsv (one row per initialiser entry and
object, in execution order).
"""
import collections
import json
import os
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402
from sweep_c13_weak import statement_around, declarator  # noqa: E402

CAT = 'c14-init'
BUILD = os.path.join(L.REPO, 'build_release')
BINARY = os.path.join(BUILD, 'src', 'observer', 'seekdb')
SYMBOLIZER = os.path.join(L.REPO, 'deps', '3rd', 'usr', 'local', 'oceanbase', 'devtools', 'bin', 'llvm-symbolizer')
ORDER_OUT = os.path.join(L.SWEEP_DIR, 'c14-init-order.tsv')
RX_ATTR = re.compile(r'__attribute__\s*\(\(\s*(constructor|init_priority)\b\s*(\(\s*([\w]+)\s*\))?')
RX_POOL_DECL = re.compile(
    r'(?m)^[ \t]*((?:static\s+|mutable\s+)*)((?:\w+::)*(\w*ObjPool|ObServerObjectPool|ObPooledAllocator|ObPool|'
    r'ObTabletMemtableMgrPool|ObIOCBPool|ObMemtableCtxObjPool|ObResourcePool)\b(?:\s*<[^;{}()]*>)?)\s+([A-Za-z_]\w*)\s*'
    r'(?:\[[^\]]*\])?\s*(?:\{[^;{}]*\}|\([^;{}]*\))?\s*;')
RX_POOL_MACRO = re.compile(r'\b(sop_borrow|sop_return|op_alloc|op_free|op_reclaim_alloc|op_reclaim_free|rp_alloc|rp_free|'
                           r'op_alloc_args)\s*\(')
POOL_ANCHORS = (  # RULEBOOK 0.9: tx ctx pool limit, log callback pool; (file, regex, symbol)
    ('src/storage/tx/ob_trans_factory.cpp', r'ObTxCtx \*ObTxCtxFactory::alloc\(\)', 'ObTxCtxFactory::alloc#limit:MAX_TX_CTX_COUNT'),
    ('src/storage/tx/ob_trans_define.h', r'MAX_TX_CTX_COUNT\s*=', 'MAX_TX_CTX_COUNT'),
    ('src/storage/tx/ob_tx_ctx_log_cb_helper.cpp', r'^int alloc_log_cb\(', 'alloc_log_cb'),
    ('src/storage/tx/ob_tx_ctx_log_cb_helper.cpp', r'^void free_log_cb\(', 'free_log_cb'),
    ('src/storage/tx/ob_tx_ctx_log_cb_helper.cpp', r'^int ObTxCtx::get_log_cb_\(', 'ObTxCtx::get_log_cb_#limit:_trx_max_log_cb_limit'),
)
N_STAB, N_TYPE, N_SECT, N_OSO, N_FUN = 0xe0, 0x0e, 0x0e, 0x66, 0x24


def macho_init_entries(path):
    """[(index, address, [symbol names], (oso_path, oso_mtime) or None)] in __init_offsets order."""
    with open(path, 'rb') as fh:
        data = fh.read()
    magic, _ct, _cs, _ft, ncmds, _sz, _fl, _r = struct.unpack_from('<IiiIIIII', data, 0)
    if magic != 0xfeedfacf:
        sys.exit('c14: %s is not a 64-bit Mach-O file' % path)
    off, text_vmaddr, init_sect, symtab = 32, None, None, None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            segname = data[off + 8:off + 24].rstrip(b'\0').decode()
            vmaddr = struct.unpack_from('<Q', data, off + 24)[0]
            nsects = struct.unpack_from('<I', data, off + 64)[0]
            if segname == '__TEXT':
                text_vmaddr = vmaddr
            so = off + 72
            for _j in range(nsects):
                sectname = data[so:so + 16].rstrip(b'\0').decode()
                addr, size, offset = struct.unpack_from('<QQI', data, so + 32)
                if sectname in ('__init_offsets', '__mod_init_func'):
                    init_sect = (sectname, addr, size, offset)
                so += 80
        elif cmd == 0x2:  # LC_SYMTAB
            symtab = struct.unpack_from('<IIII', data, off + 8)
        off += cmdsize
    if not init_sect or init_sect[0] != '__init_offsets':
        sys.exit('c14: no __TEXT,__init_offsets section (a __mod_init_func binary needs pointer decoding)')
    _n, _a, size, foff = init_sect
    addrs = [text_vmaddr + o for o in struct.unpack_from('<%dI' % (size // 4), data, foff)]
    symoff, nsyms, stroff, _ss = symtab
    names_at = collections.defaultdict(set)
    fun_oso = {}
    cur = None
    for k in range(nsyms):
        strx, ntype, nsect, _nd, nvalue = struct.unpack_from('<IBBHQ', data, symoff + 16 * k)
        end = data.index(b'\0', stroff + strx)
        name = data[stroff + strx:end].decode('utf-8', 'replace')
        if ntype & N_STAB:
            if ntype == N_OSO:
                cur = (name, nvalue)
            elif ntype == N_FUN and nsect and name:
                fun_oso.setdefault(nvalue, cur)
            continue
        if (ntype & N_TYPE) == N_SECT:
            names_at[nvalue].add(name)
    return [(i, a, sorted(names_at.get(a, ())), fun_oso.get(a)) for i, a in enumerate(addrs)]


def build_objects():
    """{(basename, mtime): repo-relative object path} for build_release/src/**/*.o, and object -> TU source."""
    by_key = collections.defaultdict(list)
    for d, _dn, fn in os.walk(os.path.join(BUILD, 'src')):
        for f in fn:
            if f.endswith('.o'):
                p = os.path.join(d, f)
                by_key[(f, int(os.stat(p).st_mtime))].append(L.rel(p))
    with open(os.path.join(BUILD, 'compile_commands.json'), encoding='utf-8') as fh:
        cc = json.load(fh)
    tu_of = {}
    for e in cc:
        obj = 'build_release/' + e['output'] if not os.path.isabs(e['output']) else L.rel(e['output'])
        tu_of[obj] = L.rel(e['file']) if e['file'].startswith(L.REPO) else e['file']
    return by_key, tu_of


def unity_members(tu):
    if not tu.endswith('.cxx'):
        return [tu]
    out = []
    with open(os.path.join(L.REPO, tu), encoding='utf-8', errors='replace') as fh:
        for m in re.finditer(r'(?m)^#include "([^"]+)"', fh.read()):
            out.append(L.rel(m.group(1)) if m.group(1).startswith(L.REPO) else m.group(1))
    return out


def norm_debug_path(p):
    seg = p.split('/./')[-1]
    while seg.startswith('./'):
        seg = seg[2:]
    return seg


def object_symbols(obj):
    out = subprocess.run(['nm', '-n', os.path.join(L.REPO, obj)], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL).stdout.decode('utf-8', 'replace')
    syms = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and re.match(r'^[0-9a-f]+$', parts[0]):
            syms.append((int(parts[0], 16), parts[1], parts[2]))
    return syms


def variables_of(obj, init_name):
    """[(file, line, col, var_init_fn, inlined callee)] in instruction order for one initialiser of obj."""
    syms = object_symbols(obj)
    starts = sorted({a for a, _t, _n in syms})
    want = [s for s in syms if s[2] == init_name or s[2].lstrip('_') == init_name.lstrip('_')]
    want += [s for s in syms if 'cxx_global_var_init' in s[2]]
    addrs = []
    for a, _t, _n in sorted(set(want)):
        nxt = [x for x in starts if x > a]
        end = nxt[0] if nxt else a + 4
        addrs.extend(range(a, end, 4))
    if not addrs:
        return [], 0
    out = subprocess.run([SYMBOLIZER, '--obj=' + os.path.join(L.REPO, obj), '--inlining', '--functions=linkage',
                          '--output-style=JSON'], input='\n'.join(hex(a) for a in addrs), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, universal_newlines=True).stdout
    groups = collections.OrderedDict()  # var_init function -> [first (file, line, col), callee]
    unlocated = set()
    for ln in out.splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        frames = d.get('Symbol', [])
        for i, fr in enumerate(frames):
            fname = fr.get('FunctionName', '')
            if not fname.startswith('__cxx_global_var_init'):
                continue
            if not fr.get('Line'):
                unlocated.add(fname)
                continue
            if fname not in groups:
                callee = frames[i - 1]['FunctionName'] if i > 0 else ''
                groups[fname] = (norm_debug_path(fr['FileName']), fr['Line'], fr.get('Column', 0), fname, callee)
            break
    found = list(groups.values())
    return found, len(unlocated - set(groups))


RX_QNAME = re.compile(r'(?:::)?[A-Za-z_]\w*(?:\s*::\s*~?[A-Za-z_]\w*)*')


def var_name(path, line, col):
    """(name, declaration line) of the object whose initialiser code is at path:line:col."""
    if not os.path.exists(os.path.join(L.REPO, path)):
        return '?', line
    lines = L.raw_lines(path)
    if not 0 < line <= len(lines):
        return '?', line
    text = lines[line - 1]
    i = max(col - 1, 0)
    mm = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*\(', text)
    if mm:
        # a macro invocation at the start of the line: the object is defined by the macro expansion
        depth, j, args = 0, mm.end() - 1, text[mm.end():]
        for k in range(j, len(text)):
            if text[k] == '(':
                depth += 1
            elif text[k] == ')':
                depth -= 1
                if depth == 0:
                    args = text[j + 1:k]
                    break
        return '%s(%s)' % (mm.group(1), re.sub(r'\s+', '', args)[:60]), line
    m = RX_QNAME.match(text, i)
    if m:
        name = re.sub(r'\s+', '', m.group(0))
        rest = text[m.end():]
        if re.match(r'\s*(?:\[[^\]]*\]\s*)*(?:__attribute__\s*\(\(.*?\)\)\s*)?(?:\(|\{|=|;)', rest) and text[:i].strip():
            return name, line
    # walk back to the declaration whose initialiser contains this position (arrays, brace lists)
    st = L.stripped(path)
    off = L.line_start_offset(path, line) + i
    depth = 0
    k = off - 1
    while k >= 0:
        c = st[k]
        if c in ')}]':
            depth += 1
        elif c in '({[':
            if depth == 0:
                j = k - 1
                while j >= 0 and st[j].isspace():
                    j -= 1
                if j >= 0 and st[j] == '=':
                    j -= 1
                    while j >= 0 and st[j].isspace():
                        j -= 1
                while j >= 0 and st[j] == ']':
                    d2 = 0
                    while j >= 0:
                        if st[j] == ']':
                            d2 += 1
                        elif st[j] == '[':
                            d2 -= 1
                            if d2 == 0:
                                break
                        j -= 1
                    j -= 1
                    while j >= 0 and st[j].isspace():
                        j -= 1
                e = j + 1
                while j >= 0 and (st[j].isalnum() or st[j] in '_:'):
                    j -= 1
                cand = st[j + 1:e].strip(':')
                if cand and not re.match(r'^[A-Z][A-Z0-9_]*$', cand) and cand not in ('return', 'sizeof'):
                    return cand, L.line_of(path, j + 1)
            else:
                depth -= 1
        elif c == '=' and depth == 0 and st[k - 1:k] not in ('=', '!', '<', '>') and st[k + 1:k + 2] != '=':
            j = k - 1
            while j >= 0 and st[j].isspace():
                j -= 1
            while j >= 0 and st[j] == ']':
                while j >= 0 and st[j] != '[':
                    j -= 1
                j -= 1
                while j >= 0 and st[j].isspace():
                    j -= 1
            e = j + 1
            while j >= 0 and (st[j].isalnum() or st[j] in '_:'):
                j -= 1
            cand = st[j + 1:e].strip(':')
            if cand:
                return cand, L.line_of(path, j + 1)
            break
        elif c == ';' and depth == 0:
            break
        k -= 1
    return '?', line


def main():
    files = L.first_party()
    first_party = set(files)
    rows = []
    seen = collections.Counter()

    def add(path, line, sym, cons, pre):
        key = (path, sym, cons)
        seen[key] += 1
        if seen[key] > 1:
            sym = '%s#%d' % (sym, seen[key])
        rows.append((path, sym, cons, line, pre))

    # ---------------- B: binary initialiser order
    entries = macho_init_entries(BINARY)
    by_key, tu_of = build_objects()
    resolved = []
    for idx, addr, names, oso in entries:
        sym = next((n for n in names if 'GLOBAL__sub_I' in n), names[0] if names else '?')
        obj = None
        if oso:
            path, mtime = oso
            base = re.sub(r'^.*\(([^()]+)\)$', r'\1', path) if path.endswith(')') else os.path.basename(path)
            cands = by_key.get((base, mtime), [])
            if len(cands) == 1:
                obj = cands[0]
            elif not path.endswith(')') and path.startswith(L.REPO):
                obj = L.rel(path)
        resolved.append((idx, addr, sym, oso, obj))
    jobs = [(r[4], r[2]) for r in resolved if r[4] and r[4] in tu_of]
    with ThreadPoolExecutor(8) as ex:
        var_lists = dict(zip(jobs, ex.map(lambda j: variables_of(*j), jobs)))
    order_rows = []
    seq = 0
    var_tus = collections.defaultdict(set)
    var_first = {}
    for idx, addr, sym, oso, obj in resolved:
        tu = tu_of.get(obj, '') if obj else ''
        origin = obj or (oso[0].replace(L.REPO + '/', '') if oso else '?')
        if obj and (obj, sym) in var_lists:
            vars_, unlocated = var_lists[(obj, sym)]
            if not vars_:
                order_rows.append((str(seq), str(idx), sym, origin, tu, '', '', '', '', 'unlocated=%d' % unlocated))
                seq += 1
            for f, ln, col, vfn, callee in vars_:
                fp = f in first_party
                name, dline = var_name(f, ln, col) if fp else ('?', ln)
                order_rows.append((str(seq), str(idx), sym, origin, tu, f, str(dline), name, callee,
                                   'unlocated=%d' % unlocated if unlocated else ''))
                if fp:
                    var_tus[(f, dline, name)].add(tu)
                    var_first.setdefault((f, dline, name), (seq, callee))
                seq += 1
        else:
            note = 'third-party or system object' if not obj else 'no compile command'
            order_rows.append((str(seq), str(idx), sym, origin, tu, '', '', '', '', note))
            seq += 1
    L.write_tsv(ORDER_OUT, ['seq', 'init_index', 'init_function', 'object', 'tu', 'var_file', 'var_line', 'var_symbol',
                            'inlined_ctor', 'note'], order_rows)
    for (f, ln, name), tus in sorted(var_tus.items()):
        s0, callee = var_first[(f, ln, name)]
        pre = 'init-seq:%d;tus:%d' % (s0, len(tus))
        if callee:
            pre += ';ctor:%s' % re.sub(r'\(.*$', '', callee)[-80:]
        cons = 'dynamically initialised static data member' if '::' in name and not name.startswith('::') \
            else 'dynamically initialised namespace-scope object'
        add(f, ln, 'dyninit:%s' % name, cons, pre)
    # ---------------- A: constructor / init_priority attributes
    init_names = {}
    mangled = [r[2] for r in resolved]
    dem = subprocess.run(['c++filt'], input='\n'.join(s[1:] if s.startswith('_') else s for s in mangled) + '\n',
                         stdout=subprocess.PIPE, universal_newlines=True).stdout.splitlines()
    for (idx, addr, sym, oso, obj), d in zip(resolved, dem):
        base = re.sub(r'\(.*$', '', d).split('::')[-1]
        init_names.setdefault(base, idx)
    var_init_index = {}
    for row in order_rows:
        if row[5]:
            var_init_index.setdefault((row[5], row[7]), row[1])
    for p in files:
        if '__attribute__' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_ATTR.finditer(text):
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if text[ls:m.start()].lstrip().startswith('#'):
                continue
            s, e, term = statement_around(text, m.start())
            name, params = declarator(text[s:e])
            if not name:
                vm = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?(?:=|;|\{)', text[s:e + 1])
                name = vm.group(1) if vm else '?'
            kind = m.group(1)
            prio = m.group(3) or ''
            cons = '__attribute__((%s))' % kind
            pre = 'priority:%s' % prio if prio else 'priority:default'
            if name in init_names:
                pre += ';binary-init-index:%s' % init_names[name]
            elif (p, name) in var_init_index:
                pre += ';binary-init-index:%s' % var_init_index[(p, name)]
            else:
                pre += ';not-an-init-entry-of-the-macOS-binary'
            add(p, L.line_of(p, m.start()), name, cons, pre)
    # ---------------- C: object pools
    for p in files:
        raw = L.raw(p)
        text = None
        if 'Pool' in raw:
            text = L.stripped(p)
            for m in RX_POOL_DECL.finditer(text):
                off = m.start(4)
                cls = L.enclosing_class(p, off)
                if cls is None and L.in_function(p, off):
                    continue
                owner = cls or '<namespace>'
                if m.group(3) == 'ObPool' and not re.search(r'ObPool\s*<', m.group(2)) and cls is None:
                    continue
                add(p, L.line_of(p, off), '%s::%s' % (owner, m.group(4)), 'object pool declaration',
                    'type:%s' % re.sub(r'\s+', '', m.group(2)))
        if re.search(r'\b(sop_borrow|sop_return|op_alloc|op_free|op_reclaim_alloc|op_reclaim_free|rp_alloc|rp_free)\b', raw):
            text = text or L.stripped(p)
            for m in RX_POOL_MACRO.finditer(text):
                ls = L.line_start_offset(p, L.line_of(p, m.start()))
                if text[ls:m.start()].lstrip().startswith('#'):
                    continue
                if p.startswith('src/oblib/lib/objectpool/'):
                    continue  # the pool implementation itself (replace:mem)
                depth, i = 0, m.end() - 1
                j = i
                while j < len(text):
                    if text[j] == '(':
                        depth += 1
                    elif text[j] == ')':
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                arg = re.sub(r'\s+', '', text[i + 1:j])[:60]
                fn = L.site_owner(p, m.start())
                add(p, L.line_of(p, m.start()), '%s#%s(%s)' % (fn, m.group(1), arg), 'object pool macro use', '')
    for f, rx, sym in POOL_ANCHORS:
        lines = L.raw_lines(f)
        hit = [i + 1 for i, ln in enumerate(lines) if re.search(rx, ln)]
        if len(hit) != 1:
            sys.exit('c14: pool anchor %r in %s matched %d lines' % (rx, f, len(hit)))
        add(f, hit[0], sym, 'object pool limit anchor', 'RULEBOOK-0.9')
    n = L.finish(CAT, rows, 'A constructor/init_priority attributes + B dynamically initialised objects from the binary '
                            '(__init_offsets order, debug map, llvm-symbolizer) + C object pools; order file c14-init-order.tsv')
    by = collections.Counter(r[2] for r in rows)
    for k, v in sorted(by.items()):
        print('  %-50s %d' % (k, v))
    fp_objs = {r[4] for r in resolved if r[4]}
    print('  init entries %d (first-party objects %d, other %d); order rows %d -> %s' % (
        len(resolved), len(fp_objs), len(resolved) - sum(1 for r in resolved if r[4]), len(order_rows), L.rel(ORDER_OUT)))
    return n


if __name__ == '__main__':
    main()
