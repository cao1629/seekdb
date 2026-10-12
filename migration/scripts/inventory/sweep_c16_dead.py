#!/usr/bin/env python3
"""c16-dead: the dead code RULEBOOK 0.8 names ("不翻译的死代码"), with the "no includer / no caller"
evidence recomputed by script.

Units (RULEBOOK 0.8): ob_bucket_qsync_lock, ob_delay_free_allocator, ob_map_queue_thread_pool, ob_ms_queue
(with ob_co_seq_queue), ob_ring_array. Functions: install_ob_signal_handler, do_with_crash_restore.
Evidence per unit (pretag):
  includers   first-party files outside the dead set that #include a member (migration/depmap/edges.tsv)
  refs        files outside the dead set whose stripped text names a class/struct/function defined at
              namespace or class level in the unit (word match)
  bin_syms    defined symbols of build_release/src/observer/seekdb whose demangled name contains one of
              those class names (nm + c++filt)
Evidence per function: callers = `name(` occurrences in stripped first-party text outside its own
definitions/declarations; bin_syms as above.
"""
import collections
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c16-dead'
DEAD_UNITS = ['src/oblib/lib/lock/ob_bucket_qsync_lock', 'src/oblib/lib/allocator/ob_delay_free_allocator',
              'src/oblib/lib/thread/ob_map_queue_thread_pool', 'src/oblib/lib/queue/ob_ms_queue',
              'src/oblib/lib/queue/ob_co_seq_queue', 'src/oblib/lib/container/ob_ring_array']
DEAD_FUNCS = ['install_ob_signal_handler', 'do_with_crash_restore']
BINARY = os.path.join(L.REPO, 'build_release', 'src', 'observer', 'seekdb')


def binary_names():
    out = subprocess.run(['nm', '-U', BINARY], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode(
        'utf-8', 'replace')
    names = [ln.split()[-1] for ln in out.splitlines() if ln.strip()]
    plain = [n[1:] if n.startswith('_') else n for n in names]
    return subprocess.run(['c++filt'], input='\n'.join(plain) + '\n', stdout=subprocess.PIPE,
                          universal_newlines=True).stdout.splitlines()


def main():
    files = L.first_party()
    unit_files = collections.defaultdict(list)
    for p in L.first_party(L.CODE_EXT):
        unit_files[os.path.splitext(p)[0]].append(p)
    dead_files = {f for u in DEAD_UNITS for f in unit_files[u]}
    missing = [u for u in DEAD_UNITS if not unit_files[u]]
    if missing:
        sys.exit('c16: dead unit(s) not in the depmap: %s' % missing)
    incl = collections.defaultdict(set)
    with open(os.path.join(L.REPO, 'migration', 'depmap', 'edges.tsv'), encoding='utf-8') as fh:
        next(fh)
        for ln in fh:
            a, b = ln.split('\t')[:2]
            incl[b].add(a)
    dem = binary_names()
    rows = []
    others = [p for p in files if p not in dead_files]
    for u in DEAD_UNITS:
        members = sorted(unit_files[u])
        rep = min(members, key=lambda f: (0 if f.endswith('.h') else 1, f))
        includers = sorted({a for m in members for a in incl.get(m, ()) if a not in dead_files})
        names = []
        for m in members:
            for s in L.scopes(m):
                if s.kind == 'class' and s.name and all(c.kind in ('namespace', 'linkage') for c in L.scope_chain(m, s.open)):
                    names.append(s.name)  # top-level classes only (nested helper names are too generic)
        names = sorted(set(n for n in names if len(n) > 3))
        rx = re.compile(r'\b(%s)\b' % '|'.join(map(re.escape, names))) if names else None
        refs = sorted(p for p in others if rx and any(n in L.raw(p) for n in names) and rx.search(L.stripped(p)))
        bin_syms = sum(1 for d in dem if rx and rx.search(d))
        cls_line = 1
        for s in L.scopes(rep):
            if s.kind == 'class' and s.name in names:
                cls_line = L.line_of(rep, s.open - len(s.header) + len(s.header) - len(s.header.lstrip()))
                break
        pre = 'includers=%d;refs=%d;bin_syms=%d;classes=%s;members=%s' % (
            len(includers), len(refs), bin_syms, ','.join(names[:4]), ','.join(os.path.basename(m) for m in members))
        if includers:
            pre += ';includer-sample=%s' % ','.join(includers[:3])
        if refs:
            pre += ';ref-sample=%s' % ','.join(refs[:3])
        rows.append((rep, os.path.basename(u), 'dead unit', cls_line, pre))
    for fname in DEAD_FUNCS:
        rx = re.compile(r'\b%s\s*\(' % re.escape(fname))
        defs, calls = [], []
        for p in files:
            if fname not in L.raw(p):
                continue
            text = L.stripped(p)
            spans = []  # definition bodies (also in unselected #if branches): (name offset, body end)
            sites = []
            for m in rx.finditer(text):
                # classify textually: `<type> name(params) [quals] {` = definition, `<type> name(params);` = declaration
                depth, j = 0, m.end() - 1
                while j < len(text):
                    if text[j] == '(':
                        depth += 1
                    elif text[j] == ')':
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                k = j + 1
                while k < len(text) and (text[k].isspace() or re.match(r'(const|noexcept|override|final)\b', text[k:])):
                    mm = re.match(r'\s+|const\b|noexcept\b|override\b|final\b', text[k:])
                    k += mm.end() if mm else 1
                ls = L.line_start_offset(p, L.line_of(p, m.start()))
                before = text[ls:m.start()]
                typed = bool(re.search(r'[\w>*&]\s*$', before)) and not re.search(r'(=|return|\(|,)\s*$', before)
                if typed and k < len(text) and text[k] == '{':
                    d2, e = 0, k
                    while e < len(text):
                        if text[e] == '{':
                            d2 += 1
                        elif text[e] == '}':
                            d2 -= 1
                            if d2 == 0:
                                break
                        e += 1
                    spans.append((m.start(), e))
                    defs.append((p, m.start()))
                elif typed and k < len(text) and text[k] == ';':
                    continue  # a declaration
                else:
                    sites.append(m.start())
            for off in sites:
                if any(a <= off <= b for a, b in spans):
                    continue  # a call between overloads of the same name
                calls.append('%s:%d' % (p, L.line_of(p, off)))
        bin_syms = sum(1 for d in dem if re.search(r'(^|::)%s\(' % re.escape(fname), d))
        seen = collections.Counter()
        for p, off in sorted(defs):
            seen[p] += 1
            sym = fname + ('' if seen[p] == 1 else '#%d' % seen[p])
            pre = 'callers=%d;bin_syms=%d;definitions=%d' % (len(calls), bin_syms, len(defs))
            if calls:
                pre += ';caller-sample=%s' % ','.join(calls[:3])
            rows.append((p, sym, 'dead function', L.line_of(p, off), pre))
    n = L.finish(CAT, rows, 'RULEBOOK 0.8 dead units (6) and functions (2 names); pretag carries the recomputed '
                            'includer/reference/caller/binary-symbol counts')
    for r in rows:
        print('  %s:%s %s %s' % (r[0], r[3], r[1], r[4][:150]))
    return n


if __name__ == '__main__':
    main()
