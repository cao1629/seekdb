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

const segmenter = new Intl.Segmenter('en', { granularity: 'grapheme' });
const escapes = new Map([
  ['\\', '\\\\'], ['\0', '\\0'], ['\b', '\\b'], ['\f', '\\f'],
  ['\n', '\\n'], ['\r', '\\r'], ['\t', '\\t'], ['\v', '\\v'],
]);

function visibleText(value) {
  if (value === null) return 'NULL';
  if (typeof value !== 'string') throw new TypeError('Table values must be strings or null.');
  if (!value.length) return "''";
  return value.replace(/[\\\p{Cc}\u00ad\u061c\u200b\u200e\u200f\u2028-\u202e\u2060-\u206f\ufeff]/gu, character => {
    if (escapes.has(character)) return escapes.get(character);
    const code = character.codePointAt(0);
    return code <= 0xff ? `\\x${code.toString(16).padStart(2, '0')}`
      : `\\u${code.toString(16).padStart(4, '0')}`;
  });
}

function isWide(code) {
  return (code >= 0x1100 && code <= 0x115f)
    || (code >= 0x2329 && code <= 0x232a)
    || (code >= 0x2e80 && code <= 0xa4cf && code !== 0x303f)
    || (code >= 0xac00 && code <= 0xd7a3)
    || (code >= 0xf900 && code <= 0xfaff)
    || (code >= 0xfe10 && code <= 0xfe19)
    || (code >= 0xfe30 && code <= 0xfe6f)
    || (code >= 0xff01 && code <= 0xff60)
    || (code >= 0xffe0 && code <= 0xffe6)
    || (code >= 0x16fe0 && code <= 0x16fe4)
    || (code >= 0x17000 && code <= 0x18dff)
    || (code >= 0x1aff0 && code <= 0x1afff)
    || (code >= 0x1b000 && code <= 0x1b2ff)
    || (code >= 0x1f200 && code <= 0x1f251)
    || (code >= 0x1f300 && code <= 0x1faff)
    || (code >= 0x20000 && code <= 0x3fffd);
}

function displayWidth(text) {
  if (/^[\x20-\x7e]*$/u.test(text)) return text.length;
  let width = 0;
  for (const { segment } of segmenter.segment(text)) {
    if (/\p{Emoji_Presentation}/u.test(segment)
      || (/\ufe0f/u.test(segment) && /\p{Emoji}/u.test(segment))
      || /[0-9#*]\ufe0f?\u20e3/u.test(segment)) {
      width += 2;
      continue;
    }
    for (const character of segment.normalize('NFC')) {
      if (/[\p{Mark}\p{Cf}]/u.test(character)) continue;
      width += isWide(character.codePointAt(0)) ? 2 : 1;
    }
  }
  return width;
}

export function formatTable(columns, rows) {
  if (!Array.isArray(columns) || columns.some(column => typeof column !== 'string')) {
    throw new TypeError('Table columns must be an array of strings.');
  }
  if (!Array.isArray(rows) || rows.some(row => !Array.isArray(row) || row.length !== columns.length)) {
    throw new TypeError('Each table row must match the column count.');
  }
  if (!columns.length) return '';
  const table = [columns, ...rows].map(row => row.map(value => {
    const text = visibleText(value);
    return { text, width: displayWidth(text) };
  }));
  const widths = columns.map((_, index) => Math.max(...table.map(row => row[index].width)));
  const border = `+${widths.map(width => '-'.repeat(width + 2)).join('+')}+`;
  const line = row => `| ${row.map((cell, index) => cell.text + ' '.repeat(widths[index] - cell.width)).join(' | ')} |`;
  return [border, line(table[0]), border, ...table.slice(1).map(line), border].join('\n');
}
