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
SWEEP = os.path.join(MIGRATION, 'inventory', 'sweep', 'printf-calls.tsv')
OUT = os.path.join(MIGRATION, 'inventory', 'pre', 'printf-calls.tsv')
READ = 'READ'
VALUES = ('NOT_TRANSLATED', 'UNKNOWN', 'CLIENT_TEXT', 'LOG_TEXT', 'STREAM_TEXT', READ)

SERVER_LOG_MACROS = frozenset((
    '_OB_LOG', '_OB_LOG_RET', 'HASH_WRITE_LOG', 'HASH_WRITE_LOG_RET', '_LOG_INFO', '_LOG_WARN', '_LOG_ERROR',
    '_LOG_DEBUG', '_COMMON_LOG', '_COMMON_LOG_RET', '_LIB_LOG', '_LIB_LOG_RET', '_SHARE_LOG', '_SHARE_SCHEMA_LOG',
    '_STORAGE_LOG', '_TRANS_LOG', '_SQL_RESV_LOG', '_BOOTSTRAP_LOG', '_OB_NUM_LEVEL_LOG',
))
USER_MESSAGE_CALLEES = frozenset(('FORWARD_USER_ERROR_MSG', '_LOG_USER_MSG'))
USER_MESSAGE_MARK = 'records a user message'
STREAM_MARKS = ('writes stdout', 'writes a stream or file')
GENERIC_PRINTERS = frozenset((
    'to_string', 'databuff_print_obj', 'databuff_print_key_obj', 'databuff_print_obj_array', 'logdata_print_obj',
    'logdata_print_key_obj', 'logdata_print_value',
))
GENERIC_PRINTER_FILE = re.compile(r'(?:ob_print_utils|ob_log_print_kv)\.(?:h|cpp)$')
FUNCTION_MACROS = frozenset((
    'DEF_TYPE_STR_FUNCS_WITHOUT_ACCURACY_FOR_NON_STRING', 'DEF_TYPE_STR_FUNCS_WITHOUT_ACCURACY_FOR_STRING',
    'DEF_TYPE_STR_FUNCS_WITHOUT_ACCURACY_FOR_ODATE', 'DEF_TYPE_TEXT_FUNCS_LENGTH', 'DEF_NUMERIC_FUNCS',
))
CALLER_BUFFERS = {
    'BUF_PRINTF': 'buf',
    'DATA_PRINTF': 'buf_',
    'LOG_DATA_PRINTF': 'data',
    'TX_KV_PRINT_WITH_ERR': 'buf',
    'TX_PRINT_FUNC_WITH_ERR': 'buf',
    'BUF_PRINT_STR': 'plan_text',
    'REPORT_OUT_OF_RANGE_ERROR': 'expr_str',
}
ARGUMENT_BUFFERS = {
    'databuff_printf': 0, 'databuff_vprintf': 0, 'snprintf': 0, 'sprintf': 0, 'vsnprintf': 0, 'lnprintf': 0,
    'easy_vsnprintf': 0, '__wrap_vsnprintf': 0, '__wrap_vsprintf': 0, 'logdata_printf': 0, 'logdata_vprintf': 0,
    'ob_alloc_printf': 0, 'BUF_PRINT_CONST_STR': 1,
}
RECEIVER_CALLEES = frozenset(('append_fmt', 'assign_fmt', 'vappend', 'vappend_fmt', 'add_plan_note', 'set_extra_info',
                              'log_message_va'))
TAGS = re.compile(r'(?: \[[a-z ]+\])+$')
DEFINE_BODY = re.compile(r'(?:^|; )inside #define (\w+)')
MAX_CALL_LINES = 80


def read_tsv(path):
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    return lines[0].split('\t'), [(number, line.split('\t')) for number, line in enumerate(lines[1:], start=2) if line]


def marked(construct, mark):
    return re.search(r'(?:^|; )' + re.escape(mark) + r'(?=[;:,( ]|$)', TAGS.sub('', construct)) is not None


def tagged(construct, tag):
    match = TAGS.search(construct)
    return bool(match) and '[%s]' % tag in match.group(0)


def callee_of(construct):
    return construct.split(' call;', 1)[0]


class Source:
    def __init__(self):
        self.files = {}

    def lines(self, path):
        if path not in self.files:
            try:
                with open(os.path.join(ROOT, path), encoding='utf-8', errors='replace') as f:
                    self.files[path] = f.read().split('\n')
            except OSError:
                self.files[path] = []
        return self.files[path]

    def statement(self, path, number):
        lines = self.lines(path)
        return '\n'.join(lines[number - 1:number - 1 + MAX_CALL_LINES])


def skip_literal(text, i):
    quote = text[i]
    i += 1
    while i < len(text) and text[i] != quote:
        i += 2 if text[i] == '\\' else 1
    return i + 1


def call_arguments(text, callee):
    for match in re.finditer(r'(?<![\w$])%s\s*\(' % re.escape(callee), text):
        i, depth, start, arguments = match.end(), 0, match.end(), []
        while i < len(text):
            ch = text[i]
            if ch in '"\'':
                i = skip_literal(text, i)
                continue
            if text.startswith('//', i):
                end = text.find('\n', i)
                i = len(text) if end < 0 else end
                continue
            if text.startswith('/*', i):
                end = text.find('*/', i + 2)
                i = len(text) if end < 0 else end + 2
                continue
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                if depth == 0:
                    arguments.append(text[start:i])
                    return [' '.join(a.replace('\\\n', ' ').split()) for a in arguments]
                depth -= 1
            elif ch == ',' and depth == 0:
                arguments.append(text[start:i])
                start = i + 1
            i += 1
        return None
    return None


def receiver(text, callee):
    match = re.search(r'(\.|->)\s*%s\s*\(' % re.escape(callee), text)
    if not match:
        if re.search(r'(?<![\w$.>])%s\s*\(' % re.escape(callee), text):
            return 'this'
        return None
    i, depth = match.start(), 0
    while i > 0:
        ch = text[i - 1]
        if ch in ')]':
            depth += 1
        elif ch in '([':
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (ch.isalnum() or ch in '_.:') and not (ch == '>' and text[i - 2:i] == '->') \
                and not (ch == '-' and text[i - 1:i + 1] == '->'):
            break
        i -= 1
    found = ' '.join(text[i:match.start()].split())
    return found or None


class Placement:
    def __init__(self):
        self.translated = [self.entries('manifest.tsv', 'inputs'), self.entries('core-manifest.tsv', 'inputs')]
        self.dropped = self.entries('not-translated.tsv', 'files', 'reason')

    @staticmethod
    def entries(name, column, reason_column=None):
        header, rows = read_tsv(os.path.join(MIGRATION, name))
        index = header.index(column)
        reason = header.index(reason_column) if reason_column else None
        table = collections.defaultdict(list)
        for number, row in rows:
            for entry in row[index].split(','):
                entry = entry.strip()
                if not entry or entry == '-':
                    continue
                match = re.fullmatch(r'(.+):(\d+)-(\d+)', entry)
                if match:
                    table[match.group(1)].append((int(match.group(2)), int(match.group(3)), number,
                                                  row[reason] if reason is not None else ''))
                else:
                    table[entry].append((None, None, number, row[reason] if reason is not None else ''))
        return table

    @staticmethod
    def covering(table, path, line):
        for first, last, number, reason in table.get(path, ()):
            if first is None or first <= line <= last:
                return first, last, number, reason
        return None

    def classify(self, path, line, construct):
        if any(self.covering(table, path, line) for table in self.translated):
            return None
        found = self.covering(self.dropped, path, line)
        if found:
            first, last, number, reason = found
            span = '' if first is None else ', lines %d-%d' % (first, last)
            return 'NOT_TRANSLATED', 'step 1: not-translated.tsv:%d (%s%s)' % (number, reason, span)
        if tagged(construct, 'vendored'):
            return 'NOT_TRANSLATED', 'step 1: [vendored], on no list (finding F1; s7-islands-unsafe.md 7.1)'
        if path.endswith(('.y', '.l')):
            return 'NOT_TRANSLATED', 'step 1: a .y or .l grammar, on no list (finding F1; s7-islands-unsafe.md 7.1 rule 3)'
        return 'UNKNOWN', 'step 1: placed in no list, an inventory bug to flag'


class Classifier:
    def __init__(self):
        self.placement = Placement()
        self.source = Source()

    def buffer(self, path, line, callee, text):
        if callee in CALLER_BUFFERS:
            return '%s, which %s reads from its caller' % (CALLER_BUFFERS[callee], callee)
        if callee in RECEIVER_CALLEES:
            found = receiver(text, callee)
            return None if found is None else '%s, the receiver of %s' % (found, callee)
        if callee in ARGUMENT_BUFFERS:
            arguments = call_arguments(self.source.statement(path, line), callee)
            position = ARGUMENT_BUFFERS[callee]
            if arguments and len(arguments) > position and arguments[position]:
                return '%s, argument %d of %s' % (arguments[position], position + 1, callee)
        return None

    def classify(self, row):
        path, line, symbol, construct, text = row[0], int(row[1]), row[2], row[3], row[4]
        callee = callee_of(construct)
        found = self.placement.classify(path, line, construct)
        if found:
            return found
        if marked(construct, 'overload not resolved'):
            return READ, 'read: step 2, overload not resolved: list the instantiations of the enclosing template'
        if marked(construct, USER_MESSAGE_MARK):
            return 'CLIENT_TEXT', 'step 3: %s records a user message' % callee
        if callee in USER_MESSAGE_CALLEES:
            return 'CLIENT_TEXT', 'step 3: %s, a user message' % callee
        if callee in SERVER_LOG_MACROS:
            return 'LOG_TEXT', 'step 3: %s, a server-log macro (section 5, row 7)' % callee
        for mark in STREAM_MARKS:
            if marked(construct, mark):
                return 'STREAM_TEXT', 'step 3: %s %s' % (callee, mark)
        if symbol in GENERIC_PRINTERS and GENERIC_PRINTER_FILE.search(path):
            return READ, 'read: step 4, the generic printer %s: the union over the values of its parameter type' % symbol
        if marked(construct, 'forwards a va_list'):
            return READ, 'read: step 4, inside the printf-family definition %s: the union over its calls' % symbol
        match = DEFINE_BODY.search(construct)
        if match:
            return READ, 'read: step 4, the #define body of %s: the union over its uses' % match.group(1)
        if marked(construct, 'inside to_string'):
            return READ, 'read: step 4, inside a to_string (%s): follow its explicit uses' % symbol
        if callee in FUNCTION_MACROS:
            return READ, 'read: step 4, a use of %s: follow the functions it defines' % callee
        written = self.buffer(path, line, callee, text)
        if written:
            return READ, 'read: step 4, follow the bytes in %s' % written
        return READ, 'read: step 4, follow the bytes %s writes' % callee


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sweep', default=SWEEP)
    parser.add_argument('--out', default=OUT)
    args = parser.parse_args()
    header, rows = read_tsv(args.sweep)
    classifier = Classifier()
    out_rows = []
    for _, row in rows:
        proposed, pattern = classifier.classify(row)
        out_rows.append(row + [proposed, pattern])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write('\t'.join(header + ['proposed', 'pattern']) + '\n')
        for row in out_rows:
            f.write('\t'.join(row) + '\n')
    values = collections.Counter(r[-2] for r in out_rows)
    total = len(out_rows)
    for value in VALUES:
        print('%-16s %5d' % (value, values.get(value, 0)), file=sys.stderr)
    print('READ share: %d of %d (%.1f%%)' % (values[READ], total, 100.0 * values[READ] / total), file=sys.stderr)
    steps = collections.Counter(re.match(r'(?:read: )?(step \d+)', r[-1]).group(1) for r in out_rows)
    for step, count in sorted(steps.items()):
        print('%-16s %5d' % (step, count), file=sys.stderr)


if __name__ == '__main__':
    main()
