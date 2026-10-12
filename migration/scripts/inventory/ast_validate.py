#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ast_validate.py -- cross-check decls.jsonl against the regex survey counts (stdlib only).

    python3 -I migration/scripts/inventory/ast_validate.py [--examples 3] [--json OUT]

For each construct the surveys counted with a regex (migration/feasibility/scripts/arch/
unsafe_share.py, migration/design/survey/scripts/memory/mem_census.py, ...) it prints

  survey   the number quoted in RULEBOOK.md / 01-depmap-report.md / the survey docs
  regex    the same regex re-run now over the first-party files of the depmap
           (migration/depmap/nodes.tsv), comments and string literals blanked out
  ast      the libclang count from build_release/inventory-ast/decls.jsonl (first-party
           records only, generated=0)

then pairs regex hits with AST sites by file and line span and lists examples of sites
only one side sees.  Output is deterministic.
"""

import argparse
import bisect
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
DECLS = os.path.join(ROOT, 'build_release', 'inventory-ast', 'decls.jsonl')
NODES = os.path.join(ROOT, 'migration', 'depmap', 'nodes.tsv')

RX = {
    # migration/feasibility/scripts/arch/unsafe_share.py:20
    'raw_ptr_member': r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*\*+\s*(const\s+)?\w+_\s*(\[[^\]]*\])?\s*;',
    # migration/feasibility/scripts/arch/unsafe_share.py:21
    'ptr_ref_slot': r'\b[A-Z]\w*\s*\*\s*&\s*\w+',
    # migration/feasibility/scripts/arch/unsafe_share.py:15
    'flex_array': r'\w+\s+\w+\s*\[\s*0\s*\]\s*;',
    # migration/design/survey/scripts/memory/mem_census.py:57
    'obstring_member': r'(?m)^\s+(const\s+)?(common::)?ObString\s+\w+_\s*(\[[^\]]*\])?\s*;',
    # no survey script was found for the next four; reconstructed in the same style
    'ref_member': r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*&\s*\w+_\s*;',
    'void_ptr_member': r'(?m)^\s+(const\s+)?void\s*\*\s*(const\s+)?\w+_\s*;',
    'virtual_inheritance': r'(?:\bvirtual\s+(?:public|protected|private)\b|\b(?:public|protected|private)\s+virtual\b)',
    'packed': r'__attribute__\s*\(\(\s*(?:__)?packed(?:__)?\s*\)\)',
}

SURVEY = {
    'raw_ptr_member': 6120, 'ptr_ref_slot': 9454, 'flex_array': 70, 'obstring_member': 1032,
    'ref_member': 1278, 'void_ptr_member': 121, 'virtual_inheritance': 2, 'packed': 64,
}

LABEL = {
    'raw_ptr_member': 'raw pointer members',
    'ptr_ref_slot': 'T *& parameters',
    'flex_array': 'flexible array members',
    'obstring_member': 'ObString members',
    'ref_member': 'reference members',
    'void_ptr_member': 'void * members',
    'virtual_inheritance': 'virtual inheritance',
    'packed': 'packed records',
}

AST_DEF = {
    'raw_ptr_member': 'fields whose canonical type is an object pointer, or an array of them (T *x[N], T *x[])',
    'ptr_ref_slot': 'parameters of type (const) T *& (not T *const &) in every declaration record (declarations and definitions)',
    'flex_array': 'last field of type T[] or T[0] (is_flexible_array)',
    'obstring_member': 'fields whose canonical type is (const) oceanbase::common::ObString or an array of it',
    'ref_member': 'fields of lvalue or rvalue reference type',
    'void_ptr_member': 'fields of type (cv) void *',
    'virtual_inheritance': 'record definitions with a virtual base',
    'packed': 'record definitions with packed=1 (attribute, #pragma pack, or reduced alignment)',
}

FUNC_KINDS = ('function', 'method', 'ctor', 'dtor', 'conversion', 'function_template')
RECORD_KINDS = ('class', 'struct', 'union', 'class_template', 'partial_specialization')
OBSTRING = 'oceanbase::common::ObString'


def strip_comments(text):
    """Blank out comments and string/char literals, keeping newlines (line numbers stay)."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            j = text.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = j
        elif c == '/' and i + 1 < n and text[i + 1] == '*':
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in text[i:j]))
            i = j
        elif c in '"\'':
            j = i + 1
            while j < n and text[j] != c and text[j] != '\n':
                j += 2 if text[j] == '\\' else 1
            j = min(j + 1, n)
            out.append(c + ' ' * max(0, j - i - 2) + (c if j - i >= 2 else ''))
            i = j
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def regex_hits(files):
    comp = {k: re.compile(v) for k, v in RX.items()}
    hits = {k: [] for k in RX}
    for rel in files:
        try:
            with open(os.path.join(ROOT, rel), encoding='utf-8', errors='replace') as f:
                text = strip_comments(f.read())
        except OSError:
            continue
        starts = [0] + [m.end() for m in re.finditer('\n', text)]
        for k, rx in comp.items():
            for m in rx.finditer(text):
                g = m.group(0)
                pos = m.start() + len(g) - len(g.lstrip())      # first non-blank char of the hit
                hits[k].append((rel, bisect.bisect_right(starts, pos), ' '.join(g.split())[:80]))
    return hits


def ast_sites():
    """Per category: list of (file, first_line, last_line, label, weight); weight = how many
    constructs the site stands for (T *& parameters of one declaration)."""
    s = {k: [] for k in RX}
    extra = {'pointer_fields': 0, 'pointer_fields_named_x_': 0, 'pointer_array_fields': 0,
             'function_pointer_fields': 0, 'ref_to_pointer_params_all_records': 0,
             'ref_to_pointer_params_unique_functions': 0, 'functions_with_ref_to_pointer_param': 0,
             'packed_by': {}, 'packed_fields': 0}
    funcs = {}
    with open(DECLS, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if r.get('generated'):
                continue
            k = r['kind']
            fl, ln = r['file'], r['line']
            if k == 'field':
                ck = r.get('canonical_kind')
                if ck == 'pointer':
                    extra['pointer_fields'] += 1
                    if r.get('name', '').endswith('_'):
                        extra['pointer_fields_named_x_'] += 1
                    s['raw_ptr_member'].append((fl, ln, ln, r['qname'], 1))
                    if r.get('pointee_qname') == 'void':
                        s['void_ptr_member'].append((fl, ln, ln, r['qname'], 1))
                elif ck in ('constant_array', 'array', 'incomplete_array') and r.get('element_kind') == 'pointer':
                    extra['pointer_array_fields'] += 1          # T *x_[N] (N may be template-dependent)
                    s['raw_ptr_member'].append((fl, ln, ln, r['qname'], 1))
                elif ck == 'function_pointer':
                    extra['function_pointer_fields'] += 1
                if ck in ('lvalue_ref', 'rvalue_ref'):
                    s['ref_member'].append((fl, ln, ln, r['qname'], 1))
                ct = r.get('canonical_type', '').replace('const ', '')
                if ct == OBSTRING or (ck == 'constant_array' and r.get('element_type', '').replace('const ', '') == OBSTRING):
                    s['obstring_member'].append((fl, ln, ln, r['qname'], 1))
                if r.get('is_flexible_array'):
                    s['flex_array'].append((fl, ln, ln, r['qname'], 1))
                if r.get('packed'):
                    extra['packed_fields'] += 1
            elif k in FUNC_KINDS:
                ps = r.get('params', [])
                n = sum(1 for p in ps if p.get('kind') == 'lvalue_ref' and p.get('pointee_kind') == 'pointer'
                        and not p.get('pointee_const'))
                if n:
                    extra['ref_to_pointer_params_all_records'] += n
                    funcs[r['usr']] = n
                    end = min(r.get('end_line', ln), ln + len(ps) + 2)
                    s['ptr_ref_slot'].append((fl, ln, end, r['qname'], n))
            elif k in RECORD_KINDS and r.get('is_definition'):
                if any(b.get('virtual') for b in r.get('bases', [])):
                    s['virtual_inheritance'].append((fl, ln, ln + 3, r['qname'], 1))
                if r.get('packed'):
                    s['packed'].append((fl, ln, r.get('end_line', ln), r['qname'], 1))
                    pb = r.get('packed_by', '?')
                    extra['packed_by'][pb] = extra['packed_by'].get(pb, 0) + 1
    extra['ref_to_pointer_params_unique_functions'] = sum(funcs.values())
    extra['functions_with_ref_to_pointer_param'] = len(funcs)
    return s, extra


def pair(hits, sites):
    """Match regex hits to AST sites (same file, hit line inside the site's line span,
    innermost site first).  Returns (unmatched hits, unmatched sites)."""
    by_file = {}
    for i, (f, a, b, _, _) in enumerate(sites):
        by_file.setdefault(f, []).append((a, b, i))
    for v in by_file.values():
        v.sort()
    used = set()
    ronly = []
    for h in sorted(set((f, l) for f, l, _ in hits)):
        f, l = h
        spans = by_file.get(f, [])
        j = bisect.bisect_right(spans, (l, 1 << 30, 1 << 30)) - 1
        hit = None
        while j >= 0 and l - spans[j][0] <= 2000:
            a, b, i = spans[j]
            if a <= l <= b:
                hit = i
                break
            j -= 1
        if hit is None:
            ronly.append(h)
        else:
            used.add(hit)
    aonly = [sites[i] for i in range(len(sites)) if i not in used]
    return ronly, sorted(aonly)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--examples', type=int, default=3)
    ap.add_argument('--json', help='also write the result as JSON here')
    o = ap.parse_args(argv)
    with open(NODES, encoding='utf-8') as f:
        next(f)
        files = sorted(l.split('\t')[0] for l in f if l.strip())
    hits = regex_hits(files)
    sites, extra = ast_sites()
    res = {'extra': extra, 'categories': {}}
    print('%-24s %7s %7s %7s  %s' % ('construct', 'survey', 'regex', 'ast', 'regex-only sites / ast-only sites'))
    for k in RX:
        hs = hits[k]
        ss = sites[k]
        ast_n = sum(x[4] for x in ss)
        ronly, aonly = pair(hs, ss)
        rset = set(ronly)
        ex_r, seen = [], set()
        for f, l, t in sorted(hs):
            if (f, l) in rset and (f, l) not in seen:
                seen.add((f, l))
                ex_r.append('%s:%d `%s`' % (f, l, t))
        ex_a = ['%s:%d %s' % (f, a, q) for f, a, b, q, w in aonly]
        res['categories'][k] = {
            'label': LABEL[k], 'ast_definition': AST_DEF[k], 'regex': RX[k], 'survey': SURVEY[k],
            'regex_hits': len(hs), 'ast': ast_n, 'regex_only_sites': len(ronly), 'ast_only_sites': len(aonly),
            'regex_only_examples': ex_r[:o.examples], 'ast_only_examples': ex_a[:o.examples]}
        print('%-24s %7d %7d %7d  %d / %d' % (LABEL[k], SURVEY[k], len(hs), ast_n, len(ronly), len(aonly)))
        for e in ex_r[:o.examples]:
            print('    regex-only  %s' % e)
        for e in ex_a[:o.examples]:
            print('    ast-only    %s' % e)
    print('extra:', json.dumps(extra, sort_keys=True))
    if o.json:
        with open(o.json, 'w', encoding='utf-8') as f:
            json.dump(res, f, sort_keys=True, indent=1)
            f.write('\n')


if __name__ == '__main__':
    main()
