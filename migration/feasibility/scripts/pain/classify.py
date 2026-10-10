import re, sys, json, collections

import os; RAW = os.environ.get('RAW', 'nomerge_raw.txt')
OUT = os.environ.get('OUT', '.') + '/'

recs = []
for chunk in open(RAW, encoding='utf-8', errors='replace').read().split('\x1e'):
    if not chunk.strip():
        continue
    parts = chunk.split('\x1f')
    h, date, author, subj = parts[0].strip(), parts[1], parts[2], parts[3]
    body = parts[4] if len(parts) > 4 else ''
    recs.append(dict(h=h, date=date, author=author, subj=subj, body=body))

def clean_body(b):
    # strip PR-template boilerplate: HTML comments, template headings, co-author trailers
    b = re.sub(r'<!--.*?-->', ' ', b, flags=re.S)
    lines = []
    for ln in b.splitlines():
        s = ln.strip()
        if re.match(r'^#+\s*(Task Description|Solution Description|Passed Regressions|Upgrade Compatibility|Other Information|Release Note)', s, re.I):
            continue
        if re.match(r'^(Co-authored-by|Signed-off-by):', s, re.I):
            continue
        lines.append(ln)
    return '\n'.join(lines)

FIX_RE = re.compile(r'\b(fix|fixes|fixed|fixing|bug|bugs|bugfix|fixbug|hotfix|crash|crashes|crashed|core ?dump|coredump|cores?\b|segfault|regression|regressions|leak|leaks|deadlock|race|overflow|null ?pointer|revert)\b|修复|崩溃|死锁|泄[漏露]', re.I)

# class patterns (applied on subject; and separately on subject+cleaned body)
P = collections.OrderedDict()
P['mem:use-after-free'] = r'use[- ]after[- ]free|\buaf\b|heap-use-after-free|after (being )?freed|freed (memory|object|buffer)|already (been )?(freed|released|destroyed)'
P['mem:double-free'] = r'double[- ](free|delete|release|destroy)|(freed|released|deleted) twice'
P['mem:overflow/oob'] = r'buffer overflow|heap[- ]overflow|stack[- ]overflow|heap-buffer-overflow|stack-buffer-overflow|out[- ]of[- ]bounds?|\boob\b|index out of range|array index|越界|buffer overrun|overrun|overread|over-read'
P['mem:uninit'] = r'uninit|uninitiali[sz]ed|not (been )?initiali[sz]ed|use of uninitiali|未初始化|uninitialised'
P['mem:null-deref'] = r'null[- ]?pointer|\bnullptr\b|null deref|null ptr|\bnpe\b|空指针|null check|NULL (pointer|ptr)|dereferenc'
P['mem:dangling/lifetime'] = r'dangling|wild pointer|野指针|悬空|lifetime|life[- ]?cycle|stale pointer|stale reference|invalid pointer|invalid memory'
P['mem:leak'] = r'\bleak(s|ed|ing)?\b|泄[漏露]|memory not (freed|released)|内存没有清理'
P['conc:race'] = r'\brace\b|race condition|data race|\bracy\b|concurren|thread[- ]safe|thread[- ]unsafe|atomicity|\batomic(ally)?\b|并发|竞争'
P['conc:deadlock/hang'] = r'dead[- ]?lock|死锁|livelock|\bhang(s|ing)?\b|\bstuck\b|lock cycle|lock order'
P['crash'] = r'\bcrash(es|ed|ing)?\b|core ?dump|coredump|\bcores?\b|segfault|sigsegv|sigabrt|\babort(s|ed)?\b|崩溃'

CP = {k: re.compile(v, re.I) for k, v in P.items()}

# things that clearly are not runtime-bug fixes in this sense
NONRUNTIME = re.compile(r'\b(doc|docs|readme|ci|build|compile|compil|compilation|package|pkg|packaging|deb|rpm|msi|cmake|bazel|lint|format|formatting|typo|license|workflow|farm|test case|testcase|mysqltest|case)\b', re.I)

rows = []
for r in recs:
    subj = r['subj']
    body = clean_body(r['body'])
    is_fix_subj = bool(FIX_RE.search(subj))
    is_fix_body = bool(FIX_RE.search(body))
    s_hits = [k for k, c in CP.items() if c.search(subj)]
    b_hits = [k for k, c in CP.items() if c.search(subj + '\n' + body)]
    rows.append(dict(r, is_fix_subj=is_fix_subj, is_fix_body=is_fix_body, s_hits=s_hits, b_hits=b_hits,
                     nonruntime=bool(NONRUNTIME.search(subj))))

json.dump(rows, open(OUT + 'rows.json', 'w'), ensure_ascii=False)

fix = [x for x in rows if x['is_fix_subj']]
print('non-merge commits:', len(rows))
print('fix-like by subject:', len(fix))
print('fix-like by subject or body:', sum(1 for x in rows if x['is_fix_subj'] or x['is_fix_body']))

def primary(hits):
    mem = [h for h in hits if h.startswith('mem:')]
    conc = [h for h in hits if h.startswith('conc:')]
    if mem:
        return 'memory'
    if conc:
        return 'concurrency'
    if 'crash' in hits:
        return 'crash-nocause'
    return 'other'

c_subj = collections.Counter()
sub_subj = collections.Counter()
for x in fix:
    c_subj[primary(x['s_hits'])] += 1
    for h in x['s_hits']:
        sub_subj[h] += 1
print('\nSUBJECT-only classification of fix commits:', dict(c_subj))
print('subclass hits (subject):', dict(sub_subj))

c_body = collections.Counter()
sub_body = collections.Counter()
for x in fix:
    c_body[primary(x['b_hits'])] += 1
    for h in x['b_hits']:
        sub_body[h] += 1
print('\nSUBJECT+BODY classification of fix commits:', dict(c_body))
print('subclass hits (subject+body):', dict(sub_body))

with open(OUT + 'fix_subject_hits.tsv', 'w') as f:
    for x in fix:
        f.write('\t'.join([x['h'], x['date'], x['author'], primary(x['s_hits']), primary(x['b_hits']), ','.join(x['s_hits']), ','.join(x['b_hits']), x['subj']]) + '\n')
