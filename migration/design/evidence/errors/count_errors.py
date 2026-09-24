#!/usr/bin/env python3
# Counts for the errors research report (02-errors.md). Read-only over the tree.
# Usage: python3 count_errors.py > counts.txt
import collections
import fnmatch
import os
import re
import subprocess

ROOT = '/Users/colin/seekdb-dev/migrate-to-rust'
CODE_EXTS = ('.h', '.hpp', '.cpp', '.cc', '.c', '.ipp', '.inc')
GENERATED = {
    'src/share/ob_errno.cpp', 'src/share/ob_errno.h',
    'src/oblib/lib/ob_errno.h', 'src/share/mysql_errno.h',
}

CORE = [
    'oblib/lib/%s/' % d for d in ('alloc', 'allocator', 'container', 'hash', 'string', 'rc', 'lock', 'atomic', 'list', 'queue')
] + [
    'oblib/lib/thread/', 'oblib/lib/utility/', 'share/io/', 'oblib/lib/file/', 'oblib/lib/restore/', 'share/cache/',
    'storage/scheduler/', 'data_plane/api/data_plane/scheduler/',
    'oblib/common/object/', 'oblib/common/datum/', 'share/datum/', 'share/rc/',
    'sql/resolver/expr/', 'sql/resolver/dml/*stmt*',
    'query/api/query/engine/', 'sql/engine/ob_operator*', 'sql/engine/ob_exec_context*', 'sql/engine/ob_physical_plan*',
    'sql/engine/expr/ob_expr_frame_info*', 'sql/engine/basic/ob_pushdown_filter*', 'sql/code_generator/',
    'storage/tablet/', 'storage/meta_mem/', 'storage/memtable/', 'storage/tx/', 'storage/multi_data_source/',
    'storage/tx_table/', 'data_plane/api/data_plane/access/',
]
SQL_TIER = ['sql/optimizer/', 'sql/rewrite/', 'sql/resolver/', 'sql/engine/', 'sql/das/']


def match(rel, pats):
    for p in pats:
        if '*' in p:
            if fnmatch.fnmatch(rel, p):
                return True
        elif rel.startswith(p):
            return True
    return False


def area(path):
    rel = path[len('src/'):]
    if match(rel, CORE):
        return 'core'
    if match(rel, SQL_TIER):
        return 'sql-tier'
    return 'other'


def git_files():
    out = subprocess.run(['git', '-C', ROOT, 'ls-files', 'src'], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split('\n') if f.endswith(CODE_EXTS) and f not in GENERATED]


# ---------------------------------------------------------------- catalog
def load_catalog():
    mysql = {}
    for line in open(os.path.join(ROOT, 'src/share/mysql_errno.h'), encoding='utf-8', errors='replace'):
        m = re.match(r'#define\s+(ER_\w+|CR_\w+|WARN_\w+)\s+(\d+)', line)
        if m:
            mysql[m.group(1)] = int(m.group(2))
    entries = []
    other = []
    rx = re.compile(r'^(DEFINE_ERROR(?:_EXT)?(?:_DEP)?)\((.*?)\)?;*\s*$')
    for n, line in enumerate(open(os.path.join(ROOT, 'src/share/ob_errno.def'), encoding='utf-8', errors='replace'), 1):
        if line.startswith('DEFINE_OTHER_MSG_FMT'):
            other.append((n, line.strip()))
            continue
        m = rx.match(line)
        if not m:
            continue
        kind = m.group(1)
        body = m.group(2)
        head = re.match(r'\s*([^,]+),\s*([^,]*),\s*([^,]*),\s*("[^"]*")\s*,\s*(.*)$', body)
        name, code, my, sqlstate, rest = head.groups()
        strings = re.findall(r'"((?:[^"\\]|\\.)*)"', rest)
        str_error = strings[0] if strings else ''
        ext = '_EXT' in kind
        str_user = strings[1] if ext and len(strings) > 1 else str_error
        extra = strings[2:] if ext else strings[1:]
        my = my.strip()
        if re.match(r'^-?\d+$', my):
            myn = int(my)
        else:
            myn = mysql.get(my, None)
        entries.append(dict(line=n, kind=kind, name=name.strip(), code=int(code), mysql_raw=my, mysql=myn,
                            sqlstate=sqlstate.strip('"'), str_error=str_error, str_user=str_user,
                            has_cause=len(extra) >= 1, has_solution=len(extra) >= 2))
    return entries, other, mysql


def catalog_report(entries, other):
    print('== catalog: src/share/ob_errno.def ==')
    kinds = collections.Counter(e['kind'] for e in entries)
    print('entry lines by macro:', dict(kinds), 'total', len(entries))
    print('DEFINE_OTHER_MSG_FMT lines:', [n for n, _ in other])
    names = collections.Counter(e['name'] for e in entries)
    print('duplicate names:', {k: v for k, v in names.items() if v > 1})
    byname = {}
    for e in entries:
        byname[e['name']] = e
    uniq = list(byname.values())
    print('distinct names:', len(uniq))
    codes = collections.Counter(e['code'] for e in uniq)
    print('duplicate codes across distinct names:', {k: v for k, v in codes.items() if v > 1})
    neg = [e for e in uniq if e['code'] < 0]
    print('negative codes:', len(neg), 'min', min(e['code'] for e in uniq), 'max', max(e['code'] for e in uniq))
    unresolved = [e for e in uniq if e['mysql'] is None]
    print('mysql errno unresolved tokens:', [(e['name'], e['mysql_raw']) for e in unresolved][:20])
    wire_ob = [e for e in neg if e['mysql'] is not None and e['mysql'] < 0]
    wire_my = [e for e in neg if e['mysql'] is not None and e['mysql'] > 0]
    print('entries whose client number is the OB code (mysql errno -1):', len(wire_ob))
    print('entries mapped to a MySQL number:', len(wire_my))
    by_my = collections.defaultdict(list)
    for e in wire_my:
        by_my[e['mysql']].append(e['name'])
    shared = {k: v for k, v in by_my.items() if len(v) > 1}
    print('distinct MySQL numbers used:', len(by_my), '; MySQL numbers shared by 2+ OB codes:', len(shared),
          '; OB codes involved:', sum(len(v) for v in shared.values()))
    top = sorted(shared.items(), key=lambda kv: -len(kv[1]))[:8]
    for k, v in top:
        print('   mysql %d shared by %d OB codes, e.g. %s' % (k, len(v), ', '.join(v[:4])))
    ss = collections.Counter(e['sqlstate'] for e in neg)
    print('distinct SQLSTATEs:', len(ss), 'top:', ss.most_common(10))
    fmt_rx = re.compile(r'%(?:[-+ #0]*)(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|z|j|t|L)?[diouxXeEfgGcsp%]')
    fmt_entries = [e for e in neg if fmt_rx.search(e['str_user'].replace('%%', ''))]
    print('entries whose user message has a printf format:', len(fmt_entries))
    specs = collections.Counter()
    for e in neg:
        for s in fmt_rx.findall(e['str_user']):
            specs[s] += 1
    print('format specifiers in user messages:', specs.most_common())
    print('entries with an explicit cause string:', sum(1 for e in neg if e['has_cause']),
          '; with an explicit solution string:', sum(1 for e in neg if e['has_solution']))
    ext_diff = [e for e in neg if e['str_user'] != e['str_error']]
    print('entries whose user message differs from str_error:', len(ext_diff))
    rng = collections.Counter()
    for e in neg:
        c = -e['code']
        b = ('4000-4499' if c < 4500 else '4500-4999' if c < 5000 else '5000-5999' if c < 6000 else
             '6000-6999' if c < 7000 else '7000-7999' if c < 8000 else '8000-8999' if c < 9000 else
             '9000-9499' if c < 9500 else '9500-9999' if c < 10000 else '10000-10999' if c < 11000 else
             '11000-11999' if c < 12000 else '>=12000')
        rng[b] += 1
    print('entries by range of -code:', sorted(rng.items()))
    return byname


# ---------------------------------------------------------------- code scan
def scan(files, names):
    name_alt = r'(?:::)?(?:oceanbase::)?(?:common::)?'
    pats = {
        'int ret = OB_SUCCESS;': re.compile(r'\bint\s+ret\s*=\s*' + name_alt + r'OB_SUCCESS\s*;'),
        'INIT_SUCC(ret)': re.compile(r'\bINIT_SUCC\s*\(\s*ret\s*\)'),
        'OB_FAIL(': re.compile(r'\bOB_FAIL\s*\('),
        'OB_SUCC(': re.compile(r'\bOB_SUCC\s*\('),
        'OB_SUCC(ret) &&': re.compile(r'\bOB_SUCC\s*\(\s*ret\s*\)\s*&&'),
        'ret = OB_SUCCESS; (reset, whole line)': re.compile(r'^\s*ret\s*=\s*' + name_alt + r'OB_SUCCESS\s*;'),
        'OB_TMP_FAIL(': re.compile(r'\bOB_TMP_FAIL\s*\('),
        'int tmp_ret = OB_SUCCESS': re.compile(r'\bint\s+tmp_ret\s*=\s*' + name_alt + r'OB_SUCCESS'),
        'tmp_ret (word)': re.compile(r'\btmp_ret\b'),
        'tmp_ret or OB_TMP_FAIL (either)': re.compile(r'\btmp_ret\b|\bOB_TMP_FAIL\b'),
        'ret = tmp_ret;': re.compile(r'\bret\s*=\s*tmp_ret\s*;'),
        'ret = ... ? tmp_ret : ret (keep first error)': re.compile(r'\bret\s*=\s*\(?\s*(?:OB_SUCC\s*\(\s*ret\s*\)|' + name_alt + r'OB_SUCCESS\s*==\s*ret|ret\s*==\s*' + name_alt + r'OB_SUCCESS)\s*\)?\s*\?\s*tmp_ret\s*:\s*ret'),
        'COVER_SUCC(': re.compile(r'\bCOVER_SUCC\s*\('),
        'feasibility OB_X == ret pattern': re.compile(r'\bOB_\w+\s*==\s*ret\b|\bret\s*==\s*OB_(?!SUCCESS)\w+'),
        'feasibility != pattern': re.compile(r'\bOB_\w+\s*!=\s*ret\b|\bret\s*!=\s*OB_(?!SUCCESS)\w+'),
        'OB_SUCCESS == ret / ret == OB_SUCCESS': re.compile(r'\bOB_SUCCESS\s*==\s*ret\b|\bret\s*==\s*' + name_alt + r'OB_SUCCESS\b'),
        'OB_SUCCESS != ret / ret != OB_SUCCESS': re.compile(r'\bOB_SUCCESS\s*!=\s*ret\b|\bret\s*!=\s*' + name_alt + r'OB_SUCCESS\b'),
        'switch (ret)': re.compile(r'\bswitch\s*\(\s*ret\s*\)'),
        'case OB_<code>:': re.compile(r'\bcase\s+' + name_alt + r'OB_[A-Z0-9_]+\s*:'),
        'int &ret = ret_;': re.compile(r'\bint\s*&\s*ret\s*=\s*ret_\s*;'),
        'int &ret = <x>ret_; (wider)': re.compile(r'\bint\s*&\s*ret\s*=\s*[\w\.\->]*ret_\s*;'),
        'OZ(': re.compile(r'\bOZ\s*\('),
        'OX(': re.compile(r'\bOX\s*\('),
        'CK(': re.compile(r'\bCK\s*\('),
        'OV(': re.compile(r'\bOV\s*\('),
        'OZX1/OZX2(': re.compile(r'\bOZX[12]\s*\('),
        'FALSE_IT(': re.compile(r'\bFALSE_IT\s*\('),
        'OB_ISNULL(': re.compile(r'\bOB_ISNULL\s*\('),
        'ret = OB_ERR_UNEXPECTED': re.compile(r'\bret\s*=\s*' + name_alt + r'OB_ERR_UNEXPECTED\b'),
        'ret = OB_NOT_INIT': re.compile(r'\bret\s*=\s*' + name_alt + r'OB_NOT_INIT\b'),
        'ret = OB_INIT_TWICE': re.compile(r'\bret\s*=\s*' + name_alt + r'OB_INIT_TWICE\b'),
        'ret = OB_INVALID_ARGUMENT': re.compile(r'\bret\s*=\s*' + name_alt + r'OB_INVALID_ARGUMENT\b'),
        'ret = OB_ALLOCATE_MEMORY_FAILED': re.compile(r'\bret\s*=\s*' + name_alt + r'OB_ALLOCATE_MEMORY_FAILED\b'),
        'LOG_USER_ERROR(': re.compile(r'\bLOG_USER_ERROR\s*\('),
        'LOG_USER_WARN(': re.compile(r'\bLOG_USER_WARN\s*\('),
        'LOG_USER_NOTE(': re.compile(r'\bLOG_USER_NOTE\s*\('),
        'LOG_USER_ERROR_WITH_LINE_COL(': re.compile(r'\bLOG_USER_ERROR_WITH_LINE_COL\s*\('),
        'LOG_MYSQL_USER_*(': re.compile(r'\bLOG_MYSQL_USER_(?:ERROR|WARN|NOTE)\s*\('),
        'FORWARD_USER_*(': re.compile(r'\bFORWARD_USER_(?:ERROR|WARN|NOTE|ERROR_MSG)\s*\('),
        'LOG_USER_WARN_ONCE(': re.compile(r'\bLOG_USER_WARN_ONCE\s*\('),
        'append_warning( (direct)': re.compile(r'\bappend_warning\s*\('),
        'set_error( on warning buffer (direct)': re.compile(r'(?:wb|warning_buf|warning_buffer)\w*\s*(?:->|\.)\s*set_error\s*\('),
        'ob_get_tsi_warning_buffer(': re.compile(r'\bob_get_tsi_warning_buffer\s*\('),
        'ob_strerror(': re.compile(r'\bob_strerror\s*\('),
        'ob_mysql_errno( / ob_errpkt_errno(': re.compile(r'\bob_(?:mysql_errno|mysql_errno_with_check|errpkt_errno)\s*\('),
        'ob_sqlstate(': re.compile(r'\bob_sqlstate\s*\('),
        'ob_str_user_error( / ob_errpkt_str_user_error(': re.compile(r'\bob_(?:errpkt_)?str_user_error\s*\('),
        'ob_error_name(': re.compile(r'\bob_error_name\s*\('),
        'LOG_*_RET( (log with explicit code)': re.compile(r'\b[A-Z_]*LOG_RET\s*\(|\b[A-Z]+_LOG_RET\s*\(|\bLOG_(?:WARN|ERROR|INFO|TRACE|DEBUG|EDIAG|WDIAG|DBA_WARN|DBA_ERROR)_RET\s*\('),
        'RETRY_FUNC': re.compile(r'\bRETRY_FUNC(?:_ON_ERROR)?\s*\('),
        'CM_IS_WARN_ON_FAIL(': re.compile(r'\bCM_IS_WARN_ON_FAIL\s*\('),
        'SMART_CALL(': re.compile(r'\bSMART_CALL\s*\('),
        'OB_E(EventTable::': re.compile(r'\bOB_E\s*\(\s*(?:common::)?EventTable::'),
        'errsim EN_ / ERRSIM_POINT': re.compile(r'\bERRSIM_POINT_DEF\s*\(|\bOB_E\s*\(|\bEN_\w+\s*\?'),
    }
    counts = collections.Counter()
    files_hit = collections.defaultdict(set)
    area_counts = collections.defaultdict(collections.Counter)
    name_set = set(names)
    code_rx = re.compile(r'\bOB_[A-Z0-9_]+\b')
    cmp_left = re.compile(r'\b(OB_[A-Z0-9_]+)\s*(==|!=)\s*([A-Za-z_][A-Za-z0-9_]*(?:(?:\.|->)[A-Za-z_][A-Za-z0-9_]*)*(?:\(\s*\))?)')
    cmp_right = re.compile(r'([A-Za-z_][A-Za-z0-9_]*(?:(?:\.|->)[A-Za-z_][A-Za-z0-9_]*)*(?:\(\s*\))?)\s*(==|!=)\s*' + name_alt + r'(OB_[A-Z0-9_]+)\b')
    case_rx = re.compile(r'\bcase\s+' + name_alt + r'(OB_[A-Z0-9_]+)\s*:')
    assign_rx = re.compile(r'(?<![=!<>])\b(ret|tmp_ret)\s*=\s*' + name_alt + r'(OB_[A-Z0-9_]+)\s*;')
    return_rx = re.compile(r'\breturn\s+' + name_alt + r'(OB_[A-Z0-9_]+)\s*;')
    per_code = collections.defaultdict(collections.Counter)
    per_code_files = collections.defaultdict(set)
    cmp_lines_total = collections.Counter()
    cmp_other_var = collections.Counter()
    ret_ref_sites = []
    log_user_codes = collections.Counter()
    log_user_rx = re.compile(r'\b(LOG_USER_ERROR|LOG_USER_WARN|LOG_USER_NOTE|LOG_USER_ERROR_WITH_LINE_COL|LOG_MYSQL_USER_ERROR|LOG_MYSQL_USER_WARN|LOG_MYSQL_USER_NOTE|FORWARD_USER_ERROR|FORWARD_USER_WARN|FORWARD_USER_ERROR_MSG|FORWARD_USER_NOTE)\s*\(\s*' + name_alt + r'([A-Za-z_][\w:]*)')
    for path in files:
        full = os.path.join(ROOT, path)
        try:
            lines = open(full, encoding='utf-8', errors='replace').read().split('\n')
        except Exception:
            continue
        ar = area(path)
        for i, line in enumerate(lines):
            for key, rx in pats.items():
                if rx.search(line):
                    counts[key] += 1
                    files_hit[key].add(path)
                    area_counts[key][ar] += 1
            if pats['int &ret = <x>ret_; (wider)'].search(line):
                ret_ref_sites.append('%s:%d: %s' % (path, i + 1, line.strip()))
            for m in log_user_rx.finditer(line):
                log_user_codes[(m.group(1), m.group(2))] += 1
            if 'OB_' not in line:
                continue
            seen_codes_cmp = set()
            seen_var = {}
            for m in cmp_left.finditer(line):
                c, op, var = m.groups()
                if c in name_set:
                    seen_codes_cmp.add((c, op))
                    seen_var[(c, op)] = var
            for m in cmp_right.finditer(line):
                var, op, c = m.groups()
                if c in name_set and not var.startswith('OB_'):
                    seen_codes_cmp.add((c, op))
                    seen_var[(c, op)] = var
            nxt = '\n'.join(lines[i:i + 4])
            for (c, op) in seen_codes_cmp:
                var = seen_var[(c, op)]
                kind = 'ret' if var == 'ret' else 'tmp_ret' if var == 'tmp_ret' else 'other'
                per_code[c]['cmp' + op + ':' + kind] += 1
                per_code[c]['cmp_any'] += 1
                per_code_files[c].add(path)
                per_code[c]['area:' + ar] += 1
                if c != 'OB_SUCCESS' and op == '==':
                    after = nxt.split('\n', 1)
                    window = line + '\n' + (after[1] if len(after) > 1 else '')
                    if re.search(r'\bret\s*=\s*' + name_alt + r'OB_SUCCESS\s*;', window):
                        per_code[c]['then ret = OB_SUCCESS (within 3 lines)'] += 1
                    other_assign = [x for x in re.findall(r'\bret\s*=\s*' + name_alt + r'(OB_[A-Z0-9_]+)\s*;', window) if x not in ('OB_SUCCESS', c)]
                    if other_assign:
                        per_code[c]['then ret = other code (within 3 lines)'] += 1
                if kind == 'other':
                    cmp_other_var[var] += 1
            if seen_codes_cmp:
                if any(c != 'OB_SUCCESS' for c, _ in seen_codes_cmp):
                    cmp_lines_total['lines comparing a variable with a non-success code'] += 1
                    cmp_lines_total['... area ' + ar] += 1
                    if any(c != 'OB_SUCCESS' and seen_var[(c, op)] == 'ret' for c, op in seen_codes_cmp):
                        cmp_lines_total['... of which the variable is ret'] += 1
                if any(c == 'OB_SUCCESS' for c, _ in seen_codes_cmp):
                    cmp_lines_total['lines comparing a variable with OB_SUCCESS'] += 1
            for m in case_rx.finditer(line):
                c = m.group(1)
                if c in name_set:
                    per_code[c]['case'] += 1
            for m in assign_rx.finditer(line):
                c = m.group(2)
                if c in name_set:
                    per_code[c]['assign ' + m.group(1)] += 1
            for m in return_rx.finditer(line):
                c = m.group(1)
                if c in name_set:
                    per_code[c]['return'] += 1
            for c in set(code_rx.findall(line)):
                if c in name_set:
                    per_code[c]['word lines'] += 1
    return counts, files_hit, area_counts, per_code, per_code_files, cmp_lines_total, cmp_other_var, ret_ref_sites, log_user_codes


def main():
    entries, other, mysql = load_catalog()
    byname = catalog_report(entries, other)
    names = list(byname.keys()) + [re.match(r'DEFINE_OTHER_MSG_FMT\(([^,]+),', l).group(1) for _, l in other]
    files = git_files()
    print()
    print('== code scan: %d files (git ls-files src, extensions %s, minus the 4 generated errno files) ==' % (len(files), ' '.join(CODE_EXTS)))
    counts, files_hit, area_counts, per_code, per_code_files, cmp_lines_total, cmp_other_var, ret_ref_sites, log_user_codes = scan(files, names)
    for k, v in counts.items():
        print('%-55s %7d lines %5d files   core %6d  sql-tier %6d  other %6d' % (k, v, len(files_hit[k]), area_counts[k]['core'], area_counts[k]['sql-tier'], area_counts[k]['other']))
    print()
    for k, v in cmp_lines_total.items():
        print('%-60s %7d' % (k, v))
    print('distinct non-success codes compared anywhere:', sum(1 for c, d in per_code.items() if c != 'OB_SUCCESS' and d['cmp_any'] > 0))
    print('variables other than ret/tmp_ret compared with codes (top 25):', cmp_other_var.most_common(25))
    print()
    print('== per code (non-success), ranked by comparison lines ==')
    ranked = sorted(((c, d) for c, d in per_code.items() if c != 'OB_SUCCESS'), key=lambda cd: -cd[1]['cmp_any'])
    hdr = ['word lines', 'cmp_any', 'cmp==:ret', 'cmp!=:ret', 'cmp==:tmp_ret', 'cmp==:other', 'cmp!=:other', 'case', 'then ret = OB_SUCCESS (within 3 lines)', 'then ret = other code (within 3 lines)', 'assign ret', 'return', 'area:core', 'area:sql-tier', 'area:other']
    print('code\t' + '\t'.join(hdr) + '\tfiles')
    for c, d in ranked[:70]:
        print(c + '\t' + '\t'.join(str(d[h]) for h in hdr) + '\t' + str(len(per_code_files[c])))
    print()
    print('top 60 by word lines:')
    for c, d in sorted(per_code.items(), key=lambda cd: -cd[1]['word lines'])[:60]:
        print('  %-45s word lines %6d  cmp %5d  case %4d  assign ret %5d  return %4d' % (c, d['word lines'], d['cmp_any'], d['case'], d['assign ret'], d['return']))
    print()
    print('== int &ret = ...ret_; sites ==')
    for s in ret_ref_sites:
        print(s)
    print()
    print('== LOG_USER_* / FORWARD_USER_* by macro and first argument ==')
    by_macro = collections.Counter()
    codes_by_macro = collections.defaultdict(set)
    for (mac, code), n in log_user_codes.items():
        by_macro[mac] += n
        codes_by_macro[mac].add(code)
    for mac, n in by_macro.most_common():
        print('  %-32s %6d calls, %4d distinct first arguments' % (mac, n, len(codes_by_macro[mac])))
    allcodes = set()
    for mac in ('LOG_USER_ERROR', 'LOG_USER_ERROR_WITH_LINE_COL', 'LOG_MYSQL_USER_ERROR'):
        allcodes |= codes_by_macro[mac]
    print('  distinct codes with a LOG_USER_ERROR-family call:', len(allcodes))
    warn_codes = codes_by_macro['LOG_USER_WARN'] | codes_by_macro['LOG_MYSQL_USER_WARN']
    print('  distinct codes with a LOG_USER_WARN-family call:', len(warn_codes))
    non_const = [c for c in allcodes | warn_codes if not c.startswith('OB_')]
    print('  first arguments that are not OB_ names:', sorted(non_const)[:30])
    ext_names = {e['name'] for e in byname.values() if e['str_user'] != e['str_error']}
    print('  EXT entries (user message differs) never passed to a LOG_USER_* call:', len(ext_names - (allcodes | warn_codes | codes_by_macro['LOG_USER_NOTE'])))


if __name__ == '__main__':
    main()
