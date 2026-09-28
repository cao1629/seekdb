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
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MIGRATION = os.path.join(ROOT, 'migration')
SRC = os.path.join(ROOT, 'src')
SWEEP = os.path.join(MIGRATION, 'inventory', 'sweep', 'const-cast.tsv')
OUT = os.path.join(MIGRATION, 'inventory', 'pre', 'const-cast.tsv')
READ = 'READ'

IR_ROOTS = (
    'ParseNode', 'ObRawExpr', 'ObDMLStmt', 'ObSelectStmt', 'ObInsertStmt', 'ObUpdateStmt', 'ObDeleteStmt',
    'ObDelUpdStmt', 'ObExplainStmt', 'ObLockTableStmt', 'TableItem', 'JoinedTable', 'SemiInfo', 'ObRawExprSet',
    'ObLogPlan', 'ObLogicalOperator', 'Path', 'ObJoinOrder', 'ObConflictDetector', 'ObRangeNode', 'ObPLBlockNS',
)
HASH_FILES = ('src/oblib/lib/hash/ob_hashmap.h', 'src/oblib/lib/hash/ob_hashset.h')
HASH_SYMBOLS = ('ObHashMap::get_refactored', 'ObHashMap::get', 'ObHashSet::get', 'ObHashSet::exist_refactored')
REPLACED_DIRS = ('src/oblib/lib/alloc/', 'src/oblib/lib/allocator/', 'src/oblib/lib/lock/',
                 'src/oblib/lib/objectpool/')
GUARD_CANDIDATES = 'REPLACED if the guard only locks and unlocks, else LOCK_OR_ATOMIC (a)'

TYPE = r'[^<>;()]*(?:<[^<>;()]*(?:<[^<>;()]*>[^<>;()]*)*>[^<>;()]*)*'
CONST_CAST = re.compile(r'const_cast\s*<' + TYPE + r'>\s*\(')
CONST_OPERAND = re.compile(r'(?:static|reinterpret|dynamic)_cast\s*<\s*const\b' + TYPE + r'>\s*\(')
PX_SERIALIZE = re.compile(r'OB_DEF_(DE)?SERIALIZE(_SIZE)?\((ObExprFrameInfo|ObPx\w*Args)\)')
MEMBER_INIT = re.compile(r'\b\w+_\s*\(\s*const_cast<')
SLOT_WRITE = re.compile(r'\*\s*\(\s*const_cast\s*<[^;]*?>\s*\([^;]*?\)\s*\)\s*=(?!=)')
LOCK_TYPE = re.compile(r'(const\s+)?((oceanbase::)?(common|lib|share)::)?(ObLatch|ObLatchMutex|TCRWLock|SpinRWLock'
                       r'|ObSpinLock|ObRLock|ObWLock|MemtableMgrLock|ObRecursiveMutex|LockType)\s*[*&]')
FREE_CALL = re.compile(r'\b(free|ob_free|OB_DELETE\w*)\s*\(\s*([^()]*,\s*)?const_cast<')
LITERALS = (re.compile(r'const_cast<\s*(unsigned\s+)?char\s*\*\s*>\s*\(\s*"'),
            re.compile(r'\(\s*(unsigned\s+)?char\s*\*\s*\)\s*"'))
BATCH_TYPE = re.compile(r'ObBatchRows\s*\*')
SESSION_TYPE = re.compile(r'(sql::)?(ObSQLSessionInfo|ObBasicSessionInfo)\s*\*')
IR_TYPE = re.compile(r'(?:oceanbase::)?(?:sql::|pl::)?(\w+)\s*[*&]')
DECLARATION = re.compile(r'\b(?:class|struct)\s+(?:[A-Z_][A-Z0-9_]*\s+)*(\w+)\s*(?:final\s*)?:(?!:)([^;{}()]*)\{')
BASE = re.compile(r'\s*(?:virtual\s+)?public\s+(?:virtual\s+)?(?:\w+\s*::\s*)*(\w+)')
SOURCE_SUFFIXES = ('.h', '.hpp', '.hh', '.ipp', '.inl', '.cpp', '.cc', '.cxx', '.c')


def read_tsv(path):
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    return lines[0].split('\t'), [line.split('\t') for line in lines[1:] if line]


class Source:
    def __init__(self):
        self.files = {}

    def line(self, path, number):
        if path not in self.files:
            with open(os.path.join(ROOT, path), encoding='utf-8', errors='replace') as f:
                self.files[path] = f.read().split('\n')
        lines = self.files[path]
        return lines[number - 1] if 0 < number <= len(lines) else ''


def not_translated_units():
    whole, ranges = {}, collections.defaultdict(list)
    header, rows = read_tsv(os.path.join(MIGRATION, 'not-translated.tsv'))
    files, reason = header.index('files'), header.index('reason')
    for number, row in enumerate(rows, start=2):
        for entry in row[files].split(','):
            match = re.fullmatch(r'(.+):(\d+)-(\d+)', entry)
            if match:
                ranges[match.group(1)].append((int(match.group(2)), int(match.group(3)), number, row[reason]))
            else:
                whole.setdefault(entry, (number, row[reason]))
    return whole, ranges


def manifest_inputs():
    covered = set()
    for name in ('manifest.tsv', 'core-manifest.tsv'):
        header, rows = read_tsv(os.path.join(MIGRATION, name))
        inputs = header.index('inputs')
        for row in rows:
            for entry in row[inputs].split(','):
                covered.add(re.sub(r':\d+-\d+$', '', entry))
    return covered


def split_bases(clause):
    parts, depth, current = [], 0, []
    for ch in clause:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current))
    return [m.group(1) for m in (BASE.match(p) for p in parts) if m]


def ir_classes():
    declared = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(SRC, 'sql')):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(SOURCE_SUFFIXES):
                with open(os.path.join(dirpath, name), encoding='utf-8', errors='replace') as f:
                    text = f.read()
                for match in DECLARATION.finditer(text):
                    declared.append((match.group(1), split_bases(match.group(2))))
    root_of = {name: name for name in IR_ROOTS}
    grown = True
    while grown:
        grown = False
        for name, bases in declared:
            if name not in root_of:
                for base in bases:
                    if base in root_of:
                        root_of[name] = root_of[base]
                        grown = True
                        break
    return root_of


def cast_targets(construct):
    targets = []
    for entry in construct.split('; '):
        for opener, open_ch, close_ch in (('const_cast<', '<', '>'), ('C-style cast (', '(', ')')):
            if entry.startswith(opener):
                depth, i = 1, len(opener)
                while i < len(entry) and depth:
                    if entry[i] == open_ch:
                        depth += 1
                    elif entry[i] == close_ch:
                        depth -= 1
                    i += 1
                targets.append(' '.join(entry[len(opener):i - 1].split()))
    return targets


def levels(target):
    return target.count('*') + target.count('&')


def plain_name(part):
    part = part.strip()
    return part if part.startswith('operator') else re.sub(r'<.*$', '', part).strip()


def symbol_parts(symbol):
    return [plain_name(p) for p in symbol.split('::')] if symbol and symbol != '-' else []


def closing(s, i):
    depth = 0
    for j in range(i, len(s)):
        if s[j] == '(':
            depth += 1
        elif s[j] == ')':
            depth -= 1
            if depth == 0:
                return j
    return -1


def cast_operands(text, site):
    operands = []
    for match in CONST_CAST.finditer(site):
        if match.start() >= len(text):
            break
        end = closing(site, match.end() - 1)
        operands.append(site[match.end():end].strip() if end >= 0 else None)
    return operands


def forwarding(site, name):
    if not name:
        return None
    call = re.escape(name) + r'\s*\('
    cast = r'const_cast\s*<' + TYPE + r'>\s*\(\s*'
    if re.search(cast + r'this\s*\)\s*->\s*' + call, site):
        return 'non-const twin'
    const_this = r'static_cast\s*<\s*const\b' + TYPE + r'>\s*\(\s*(?:\*\s*this\s*\)\s*\.|this\s*\)\s*->)\s*'
    if re.search(cast + const_this + call, site):
        return 'const twin'
    return None


def shows_const(operand, site, name):
    if not operand:
        return None
    if operand == 'this':
        return 'this in a const overload' if forwarding(site, name) == 'non-const twin' else None
    match = CONST_OPERAND.match(operand)
    if not match:
        return None
    end = closing(operand, match.end() - 1)
    if end < 0:
        return None
    rest = operand[end + 1:].strip()
    if not rest:
        return 'a cast to const'
    inner = re.sub(r'\s+', '', operand[match.end():end])
    call = re.match(r'(?:\.|->)\s*' + re.escape(name) + r'\s*\(', rest) if name else None
    if call and inner in ('this', '*this') and closing(rest, call.end() - 1) == len(rest) - 1:
        return "the const twin's result"
    return None


class Classifier:
    def __init__(self):
        self.whole, self.ranges = not_translated_units()
        self.covered = manifest_inputs()
        self.ir = ir_classes()
        self.source = Source()

    def not_translated(self, path, number, construct):
        if path in self.whole:
            line, reason = self.whole[path]
            return 'step 1: not-translated.tsv:%d (%s)' % (line, reason)
        for first, last, line, reason in self.ranges.get(path, ()):
            if first <= number <= last:
                return 'step 1: not-translated.tsv:%d (%s, lines %d-%d)' % (line, reason, first, last)
        if '[vendored]' in construct and path not in self.covered:
            return 'step 1: [vendored], and no manifest input covers the file'
        return None

    def replaced(self, path, symbol, text):
        for prefix in REPLACED_DIRS:
            if path.startswith(prefix):
                return 'REPLACED', 'step 3: a file under %s' % prefix
        if symbol.startswith('ObPxTreeSerializer::'):
            return 'REPLACED', 'step 3: ObPxTreeSerializer, the plan sent to PX workers'
        if PX_SERIALIZE.fullmatch(symbol):
            return 'REPLACED', 'step 3: %s, the plan sent to PX workers' % symbol
        parts = symbol_parts(symbol)
        if len(parts) >= 2 and parts[-1] == parts[-2] and 'Guard' in parts[-1] and MEMBER_INIT.search(text):
            return READ, "read: step 3, a member initializer of %s's constructor: %s" % (parts[-1], GUARD_CANDIDATES)
        return None

    def operands_const(self, text, site, name, count):
        operands = cast_operands(text, site)
        if len(operands) != count:
            return None
        found = [shows_const(op, site, name) for op in operands]
        return None if None in found else sorted(set(found))

    def classify(self, row):
        path, number, symbol, construct, text = row[0], int(row[1]), row[2], row[3], row[4]
        types = cast_targets(construct)
        parts = symbol_parts(symbol)
        name = parts[-1] if parts else None
        site = text + ' ' + self.source.line(path, number + 1).strip()
        found = self.not_translated(path, number, construct)
        if found:
            return 'NOT_TRANSLATED', found
        if path in HASH_FILES and symbol in HASH_SYMBOLS:
            return 'REPLACED', 'step 2: %s, a lookup of the hash-map and hash-set ports' % symbol
        found = self.replaced(path, symbol, text)
        if found:
            return found
        if all(e.endswith('(adds const only)') for e in construct.split('; ')):
            if all(levels(t) == 1 for t in types):
                return 'ADDS_CONST', 'step 4: adds const only, one level'
            if SLOT_WRITE.search(text):
                return READ, 'read: step 5, but the line writes the slot through the cast, so the slot may be const'
            return 'ADDS_CONST', 'step 5: adds const only, a two-level target'
        matches = [IR_TYPE.fullmatch(t) for t in types]
        if types and all(m and m.group(1) in self.ir for m in matches):
            classes = ', '.join(n if self.ir[n] == n else '%s (subclass of %s)' % (n, self.ir[n])
                                for n in sorted({m.group(1) for m in matches}))
            shown = self.operands_const(text, site, name, len(types))
            if shown:
                return 'IR_ID', 'step 6: %s; the operand is %s' % (classes, ', '.join(shown))
            return READ, 'read: step 6, %s: IR_ID, or NO_CONST_DROPPED if the operand is not const' % classes
        for t in types:
            if LOCK_TYPE.fullmatch(t):
                if len(parts) >= 2 and 'Guard' in parts[-2] and parts[-1] in (parts[-2], '~' + parts[-2]):
                    return READ, "read: step 7, lock type %s in %s's constructor or destructor: %s" % (
                        t, parts[-2], GUARD_CANDIDATES)
                shown = self.operands_const(text, site, name, len(types))
                if shown:
                    return 'LOCK_OR_ATOMIC', 'step 7: lock type %s; the operand is %s' % (t, ', '.join(shown))
                return READ, ('read: step 7, lock type %s: LOCK_OR_ATOMIC, or NO_CONST_DROPPED if the operand '
                              'is not const' % t)
        match = FREE_CALL.search(text)
        if match:
            shown = self.operands_const(text, site, name, len(types))
            if shown:
                return 'FREE_OR_HANDOFF', 'step 8: passed to %s; the operand is %s' % (match.group(1), ', '.join(shown))
            return READ, ('read: step 8, passed to %s: FREE_OR_HANDOFF, or NO_CONST_DROPPED if the operand '
                          'is not const' % match.group(1))
        if any(p.search(text) for p in LITERALS):
            return 'NO_WRITE', 'step 9: a string literal'
        if any(BATCH_TYPE.fullmatch(t) for t in types):
            return READ, 'read: step 10, ObBatchRows *: OWN_BATCH or UNKNOWN (Q3)'
        for t in types:
            if SESSION_TYPE.fullmatch(t):
                return READ, 'read: step 11, %s: NO_WRITE, LOCK_OR_ATOMIC or UNKNOWN (Q2)' % t
        twin = forwarding(site, name)
        if twin == 'const twin':
            return 'UNKNOWN', 'step 12: casts the result of its const twin %s (Q1)' % name
        if twin == 'non-const twin':
            return READ, ('read: step 12, calls its non-const twin %s: UNKNOWN (Q1) if the twin writes nothing, '
                          'else PARAM_TO_MUT or UNKNOWN (Q4)' % name)
        return READ, 'read: no pattern'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sweep', default=SWEEP)
    parser.add_argument('--out', default=OUT)
    args = parser.parse_args()
    header, rows = read_tsv(args.sweep)
    classifier = Classifier()
    out_rows = [row + list(classifier.classify(row)) for row in rows]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write('\t'.join(header + ['proposed', 'pattern']) + '\n')
        for row in out_rows:
            f.write('\t'.join(row) + '\n')
    values = collections.Counter(r[-2] for r in out_rows)
    steps = collections.Counter(re.match(r'(?:read: )?(step \d+|no pattern)', r[-1]).group(1) for r in out_rows)
    total = len(out_rows)
    for value, count in values.most_common():
        print('%-16s %5d' % (value, count), file=sys.stderr)
    print('READ share: %d of %d (%.1f%%)' % (values[READ], total, 100.0 * values[READ] / total), file=sys.stderr)
    for step, count in sorted(steps.items(), key=lambda kv: (len(kv[0]), kv[0])):
        print('%-16s %5d' % (step, count), file=sys.stderr)


if __name__ == '__main__':
    main()
