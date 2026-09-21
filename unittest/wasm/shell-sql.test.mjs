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
import { CommandHistory, databaseAfterStatement, isComplete, splitStatements, takeStatement } from '../../src/wasm/shell-sql.mjs';

test('statements preserve their SQL and accept an unterminated final statement', () => {
  assert.deepEqual(splitStatements('  SELECT 1;\n SELECT 2\n'), ['SELECT 1', 'SELECT 2']);
  assert.equal(isComplete('SELECT 1;\n SELECT 2'), false);
  assert.equal(isComplete('SELECT 1;\n SELECT 2;'), true);
});

test('quoted semicolons, comment markers, and doubled quotes stay in their statement', () => {
  const sql = `SELECT ';', '-- x', '# y', '/* z */', 'it''s;', "a"";b", \`a\`\`;b\`;`;
  assert.deepEqual(splitStatements(sql), [sql.slice(0, -1)]);
  assert.equal(isComplete(sql), true);
});

test('backslash escapes quotes and backslashes inside string literals', () => {
  const sql = String.raw`SELECT 'a\';b', "c\";d", '\\'; SELECT 2;`;
  assert.deepEqual(splitStatements(sql), [String.raw`SELECT 'a\';b', "c\";d", '\\'`, 'SELECT 2']);
});

test('backslashes remain literal inside quoted identifiers', () => {
  assert.deepEqual(splitStatements('SELECT `a\\`; SELECT 2;'), ['SELECT `a\\`', 'SELECT 2']);
  assert.deepEqual(splitStatements('SELECT "a\\"; SELECT 2;', { ansiQuotes: true }),
    ['SELECT "a\\"', 'SELECT 2']);
});

test('NO_BACKSLASH_ESCAPES changes how string literals end', () => {
  const sql = String.raw`SELECT 'a\'; SELECT 2;`;
  assert.equal(isComplete(sql), false);
  assert.deepEqual(splitStatements(sql, { noBackslashEscapes: true }),
    [String.raw`SELECT 'a\'`, 'SELECT 2']);
  assert.equal(isComplete(sql, { noBackslashEscapes: true }), true);
});

test('line comments ignore delimiters and work with CRLF and at end of input', () => {
  const sql = '# first;\r\nSELECT 1 -- second;\r\n; SELECT 2; -- trailing;';
  assert.deepEqual(splitStatements(sql), ['# first;\r\nSELECT 1 -- second;', 'SELECT 2']);
  assert.equal(isComplete(sql), true);
});

test('double dash without following whitespace is SQL, not a comment', () => {
  assert.deepEqual(splitStatements('SELECT 1--2; SELECT 3;'), ['SELECT 1--2', 'SELECT 3']);
  assert.equal(isComplete('SELECT 1--2'), false);
  assert.equal(isComplete('SELECT 1; --'), true);
});

test('block comments keep quotes and semicolons opaque', () => {
  assert.deepEqual(splitStatements('/* ; \' " ` */ SELECT 1; /* ; */ SELECT 2;'),
    ['/* ; \' " ` */ SELECT 1', '/* ; */ SELECT 2']);
  assert.equal(isComplete('SELECT 1; /* trailing ; */'), true);
});

test('empty fragments and ordinary comment-only fragments do not execute', () => {
  const sql = '; /* ignored */; # ignored;\n; -- ignored;\n;';
  assert.deepEqual(splitStatements(sql), []);
  assert.equal(isComplete(sql), false);
  assert.deepEqual(splitStatements(''), []);
  assert.equal(isComplete(''), false);
});

test('executable comments are kept as SQL while empty executable comments are ignored', () => {
  assert.deepEqual(splitStatements('/*!40101 SET @a = 1 */; /*! SELECT 2 */;'),
    ['/*!40101 SET @a = 1 */', '/*! SELECT 2 */']);
  assert.equal(isComplete('/*! SELECT 2 */;'), true);
  assert.equal(isComplete('/*! SELECT 2 */'), false);
  assert.deepEqual(splitStatements('/*! */; /*!40101 */;'), []);
});

test('unterminated strings, identifiers, and comments cannot be silently executed', () => {
  for (const sql of ["SELECT 'a;", 'SELECT "a;', 'SELECT `a;', 'SELECT 1; /* open']) {
    assert.throws(() => splitStatements(sql), { name: 'SyntaxError', message: /Unterminated .+ at character \d+\./u });
    assert.equal(isComplete(sql), false);
  }
  assert.equal(isComplete("SELECT 'ends in \\"), false);
});

test('taking a statement leaves the remaining script unparsed for SQL mode changes', () => {
  const sql = String.raw`SET sql_mode = 'NO_BACKSLASH_ESCAPES'; SELECT 'a\'; SELECT 2;`;
  const first = takeStatement(sql);
  assert.equal(first.statement, "SET sql_mode = 'NO_BACKSLASH_ESCAPES'");
  const second = takeStatement(first.rest, { noBackslashEscapes: true });
  assert.equal(second.statement, String.raw`SELECT 'a\'`);
  assert.equal(takeStatement(second.rest).statement, 'SELECT 2');
});

test('taking an earlier statement does not report an unterminated later statement', () => {
  const first = takeStatement('SELECT 1; SELECT "unfinished');
  assert.deepEqual(first, { statement: 'SELECT 1', rest: ' SELECT "unfinished' });
  assert.throws(() => takeStatement(first.rest), SyntaxError);
  assert.deepEqual(takeStatement('; /* comment */; SELECT 2'), { statement: 'SELECT 2', rest: '' });
  assert.equal(takeStatement('; /* comment */; -- trailing'), undefined);
});

test('completion requires the last SQL delimiter outside comments and strings', () => {
  assert.equal(isComplete("SELECT ';'"), false);
  assert.equal(isComplete('SELECT 1 -- ;'), false);
  assert.equal(isComplete('SELECT 1 /* ; */'), false);
  assert.equal(isComplete('SELECT 1; # trailing ;'), true);
  assert.equal(isComplete('SELECT 1; ; /* trailing */'), true);
});

test('invalid input is reported without hiding programming errors', () => {
  assert.throws(() => splitStatements(null), TypeError);
  assert.throws(() => isComplete(null), TypeError);
});

test('successful USE tracks database names with comments between or after tokens', () => {
  for (const sql of [
    'USE playground /* selected */',
    'USE /* selected */ playground',
    '/* before */ use\nplayground -- selected',
    '# before\nUSE playground # after',
    'USE/* selected */playground;',
  ]) assert.equal(databaseAfterStatement(sql, 'oceanbase'), 'playground');
});

test('database context preserves quoted names and comment characters inside names', () => {
  assert.equal(databaseAfterStatement('USE `work space`', 'oceanbase'), 'work space');
  assert.equal(databaseAfterStatement('USE `a``b` /* after */', 'oceanbase'), 'a`b');
  assert.equal(databaseAfterStatement('USE `a/*b#-- c`', 'oceanbase'), 'a/*b#-- c');
  assert.equal(databaseAfterStatement('USE "work space"', 'oceanbase', { ansiQuotes: true }), 'work space');
  assert.equal(databaseAfterStatement('USE "a\\"', 'oceanbase', { ansiQuotes: true }), 'a\\');
});

test('dropping the selected database or schema clears the context', () => {
  for (const sql of [
    'DROP DATABASE playground',
    'DROP /* first */ DATABASE /* second */ playground /* last */',
    'DROP SCHEMA IF /* between */ EXISTS `playground`',
    'DROP DATABASE IF EXISTS playground # after',
  ]) assert.equal(databaseAfterStatement(sql, 'playground'), null);
  assert.equal(databaseAfterStatement('DROP DATABASE another_database', 'playground'), 'playground');
  assert.equal(databaseAfterStatement('DROP TABLE playground', 'playground'), 'playground');
  assert.equal(databaseAfterStatement('SELECT DATABASE()', 'playground'), 'playground');
});

test('seekdb executable comments can select and drop a database', () => {
  assert.equal(databaseAfterStatement('/*! USE playground */', 'oceanbase'), 'playground');
  assert.equal(databaseAfterStatement('/*!40101 USE playground */', 'oceanbase'), 'playground');
  assert.equal(databaseAfterStatement('USE /*!40101 playground */', 'oceanbase'), 'playground');
  assert.equal(databaseAfterStatement('/*! DROP DATABASE */ `playground`', 'playground'), null);
  assert.equal(databaseAfterStatement('/* USE playground */ SELECT 1', 'oceanbase'), 'oceanbase');
});

test('unrelated statements leave the prompt unchanged with large quoted data', () => {
  const value = 'x'.repeat(4 * 1024 * 1024);
  for (const sql of [`SELECT '${value}'`, `/* leading */ INSERT INTO t VALUES ('${value}')`, `/*!40101 SELECT '${value}' */`, `DROP TABLE \`${value}\``]) {
    assert.equal(databaseAfterStatement(sql, 'playground'), 'playground');
  }
});

test('context parsing preserves executable comment boundaries and quoted keyword names', () => {
  assert.equal(databaseAfterStatement('/*!40101 USE */ /* gap */ `DROP`', 'oceanbase'), 'DROP');
  assert.equal(databaseAfterStatement('/*!40101 DROP */ /*! DATABASE */ IF EXISTS `USE`', 'USE'), null);
  assert.equal(databaseAfterStatement('USE `a\\b`', 'oceanbase'), 'a\\b');
  assert.equal(databaseAfterStatement('USE "a\\b"', 'oceanbase', {noBackslashEscapes: true}), 'a\\b');
});

test('incomplete or extra context tokens cannot change the prompt', () => {
  for (const sql of ['USE', 'USE target extra', 'USE target /* open', 'USE target "open', 'DROP DATABASE target extra', 'DROP DATABASE target /* open']) {
    assert.equal(databaseAfterStatement(sql, 'target'), 'target');
  }
});

test('history navigates in execution order and restores a multiline draft', () => {
  const history = new CommandHistory();
  history.push('SELECT 1;');
  history.push('SELECT 2;');
  assert.equal(history.previous('SELECT\nunfinished'), 'SELECT 2;');
  assert.equal(history.previous('edited recalled SQL'), 'SELECT 1;');
  assert.equal(history.previous(), 'SELECT 1;');
  assert.equal(history.next(), 'SELECT 2;');
  assert.equal(history.next(), 'SELECT\nunfinished');
  assert.equal(history.next(), undefined);
});

test('history drops the oldest entries, ignores empty input, and deduplicates adjacent entries', () => {
  const history = new CommandHistory(2);
  for (const sql of ['SELECT 1;', 'SELECT 2;', 'SELECT 3;', '  SELECT 3;  ', ' \n']) history.push(sql);
  assert.equal(history.previous('draft'), 'SELECT 3;');
  assert.equal(history.previous(), 'SELECT 2;');
  assert.equal(history.previous(), 'SELECT 2;');
  assert.equal(history.next(), 'SELECT 3;');
  assert.equal(history.next(), 'draft');
});

test('executing a recalled entry resets navigation and accepts a new draft', () => {
  const history = new CommandHistory();
  assert.equal(history.previous(), undefined);
  assert.equal(history.next(), undefined);
  history.push('SELECT 1;');
  history.push('SELECT 2;');
  history.previous('old draft');
  history.push('SELECT 1;');
  assert.equal(history.previous('new draft'), 'SELECT 1;');
  assert.equal(history.next(), 'new draft');
});

test('history requires a positive integer capacity', () => {
  for (const limit of [0, -1, 1.5, Infinity, NaN]) assert.throws(() => new CommandHistory(limit), RangeError);
});

test('editing recalled SQL resets navigation and preserves the edited draft', () => {
  const history = new CommandHistory();
  history.push('SELECT 1;');
  history.push('SELECT 2;');
  history.previous('old draft');
  history.previous();
  history.resetNavigation();
  assert.equal(history.next(), undefined);
  assert.equal(history.previous('edited draft'), 'SELECT 2;');
  assert.equal(history.next(), 'edited draft');
});
