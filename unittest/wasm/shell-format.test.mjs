/*
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { formatTable, TableFormatter } from '../../src/wasm/shell-format.mjs';

test('formats a table with widths determined by headers and rows', () => {
  assert.equal(formatTable(['id', 'title'], [['1', 'Browser SQL'], ['2', 'Vector search']]), [
    '+----+---------------+',
    '| id | title         |',
    '+----+---------------+',
    '| 1  | Browser SQL   |',
    '| 2  | Vector search |',
    '+----+---------------+',
  ].join('\n'));
});

test('preserves exact numeric strings and distinguishes NULL from empty values', () => {
  assert.equal(formatTable(['value'], [[null], [''], ['18446744073709551615'], ['001.2300']]), [
    '+----------------------+',
    '| value                |',
    '+----------------------+',
    '| NULL                 |',
    "| ''                   |",
    '| 18446744073709551615 |',
    '| 001.2300             |',
    '+----------------------+',
  ].join('\n'));
});

test('aligns CJK text and fullwidth Latin letters with narrow text', () => {
  assert.equal(formatTable(['x'], [['中文'], ['Ａ'], ['abc']]), [
    '+------+',
    '| x    |',
    '+------+',
    '| 中文 |',
    '| Ａ   |',
    '| abc  |',
    '+------+',
  ].join('\n'));
});

test('combining marks preserve the original string and add no display width', () => {
  assert.equal(formatTable(['x'], [['e\u0301'], ['é']]), [
    '+---+',
    '| x |',
    '+---+',
    '| e\u0301 |',
    '| é |',
    '+---+',
  ].join('\n'));
});

test('emoji sequences, flags, and keycaps occupy one wide grapheme', () => {
  assert.equal(formatTable(['x'], [['🧑🏽‍💻'], ['🇨🇳'], ['1️⃣'], ['❤️'], ['ab']]), [
    '+----+',
    '| x  |',
    '+----+',
    '| 🧑🏽‍💻 |',
    '| 🇨🇳 |',
    '| 1️⃣ |',
    '| ❤️ |',
    '| ab |',
    '+----+',
  ].join('\n'));
});

test('newlines, tabs, control characters, and literal backslashes are visible', () => {
  assert.equal(formatTable(['x'], [['a\nb\tc\rd'], ['\\n'], ['\0\x1b'], ['\u202e']]), [
    '+------------+',
    '| x          |',
    '+------------+',
    '| a\\nb\\tc\\rd |',
    '| \\\\n        |',
    '| \\0\\x1b     |',
    '| \\u202e     |',
    '+------------+',
  ].join('\n'));
});

test('escapes control characters in column names too', () => {
  assert.equal(formatTable(['a\nb'], [['x']]), [
    '+------+',
    '| a\\nb |',
    '+------+',
    '| x    |',
    '+------+',
  ].join('\n'));
});

test('empty results still show columns and an empty column list returns no table', () => {
  assert.equal(formatTable(['id'], []), '+----+\n| id |\n+----+\n+----+');
  assert.equal(formatTable([], []), '');
});

test('rejects malformed rows instead of silently omitting their values', () => {
  assert.throws(() => formatTable(['a'], [['1', '2']]), TypeError);
  assert.throws(() => formatTable(['a'], [[1]]), TypeError);
  assert.throws(() => formatTable([null], []), TypeError);
});

test('incremental formatting preserves prior cells when a later row widens a column', () => {
  const formatter = new TableFormatter(['x', 'value']);
  const rows = [['中', '1'], ['e\u0301', null], ['🧑🏽‍💻', 'a\nb'], ['longer', '']];
  for (let index = 0; index < rows.length; index++) {
    assert.equal(formatter.append(rows[index]), true);
    assert.equal(formatter.format(), formatTable(['x', 'value'], rows.slice(0, index + 1)));
    assert.equal(formatter.format(), formatter.format());
  }
});

test('the output budget includes padding, borders, headers, and newlines', () => {
  const columns = Array.from({length: 100}, (_, index) => `c${index}`);
  const formatter = new TableFormatter(columns, {maxCharacters: 100000});
  assert.equal(formatter.append(columns.map(() => 'x'.repeat(120))), true);
  for (let index = 1; index < 4; index++) assert.equal(formatter.append(columns.map(() => 'x')), true);
  assert.equal(formatter.format().length, 98415);
  assert.equal(formatter.append(columns.map(() => 'x')), false);
  assert.equal(formatter.rowCount, 4);
  assert.equal(formatter.limited, true);
  assert.equal(formatter.format().length, 98415);
});

test('the exact character boundary accounts for Unicode width and escaped controls', () => {
  const columns = ['中', 'e\u0301'];
  const rows = [['🧑🏽‍💻', '\u202e\n'], ['Ａ', null]];
  const expected = formatTable(columns, rows);
  const exact = new TableFormatter(columns, {maxCharacters: expected.length});
  assert.equal(exact.append(rows[0]), true);
  assert.equal(exact.append(rows[1]), true);
  assert.equal(exact.format(), expected);
  const shorter = new TableFormatter(columns, {maxCharacters: expected.length - 1});
  assert.equal(shorter.append(rows[0]), true);
  assert.equal(shorter.append(rows[1]), false);
  assert.ok(shorter.format().length < expected.length);
});

test('a rejected wider row leaves the accepted table unchanged', () => {
  const expected = formatTable(['x'], [['1']]);
  const formatter = new TableFormatter(['x'], {maxCharacters: expected.length});
  assert.equal(formatter.append(['1']), true);
  assert.equal(formatter.append(['x'.repeat(120)]), false);
  assert.equal(formatter.format(), expected);
  assert.equal(formatter.append(['2']), false);
});

test('oversized escaped headers do not allocate an oversized formatted result', () => {
  const formatter = new TableFormatter(Array.from({length: 100}, () => '\u202e'.repeat(120)), {maxCharacters: 100000});
  assert.equal(formatter.limited, true);
  assert.equal(formatter.append(Array.from({length: 100}, () => 'x')), false);
  assert.equal(formatter.format(), '');
  assert.equal(new TableFormatter([], {maxCharacters: 0}).format(), '');
});

test('invalid character budgets are rejected', () => {
  for (const maxCharacters of [-1, NaN, 1.5, '100']) {
    assert.throws(() => new TableFormatter(['x'], {maxCharacters}), RangeError);
  }
});
