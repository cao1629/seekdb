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
import { formatTable } from '../../src/wasm/shell-format.mjs';

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
