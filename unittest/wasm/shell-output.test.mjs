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
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import {TableFormatter} from '../../src/wasm/shell-format.mjs';

const source = await readFile(new URL('../../src/wasm/shell.mjs', import.meta.url), 'utf8');
const functions = [
  ['const MAX_RESULT_CHARACTERS', 'const element ='],
  ['function node(', 'function elapsed('],
  ['function appendOutput(', 'function scrollOutput('],
  ['function commandOutput(', 'function updateContext('],
  ['async function executeStatement(', 'async function reconnectSession('],
  ['function updateStorage(', 'function closeMenus('],
  ['function clearOutput(', "input.addEventListener('input'"],
].map(([start, end]) => {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first);
  return source.slice(first, last);
}).join('\n');

class Element {
  children = [];
  parentNode;
  value = '';

  get textContent() { return this.value + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.replaceChildren(); this.value = value; }
  get firstElementChild() { return this.children[0]; }
  append(...children) {
    for (const child of children) {
      child.remove();
      child.parentNode = this;
      this.children.push(child);
    }
  }
  remove() {
    if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = undefined;
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = undefined;
    this.children = [];
    this.value = '';
    this.append(...children);
  }
  setAttribute() {}
}

const encoder = new TextEncoder();
function harness(events) {
  const transcript = new Element();
  const decoder = new TextDecoder();
  const welcome = new Element();
  const storageHint = new Element();
  welcome.append(storageHint);
  const options = [];
  const sandbox = {
    TableFormatter,
    performance,
    transcript,
    welcome,
    storageHint,
    storage: 'memory',
    pendingStorage: undefined,
    persistentSupported: true,
    outputLengths: new WeakMap(),
    transcriptCharacters: 0,
    activeEntry: undefined,
    state: 'running',
    input: {focus() {}},
    element: () => ({textContent: 'seekdb [oceanbase]>'}),
    document: {
      createElement: () => new Element(),
      createTextNode(text) { const node = new Element(); node.textContent = text; return node; },
    },
    decode: value => value === null ? null : decoder.decode(value),
    elapsed: () => '0.000 s',
    scrollOutput() {},
    updateContext() {},
    SqlError: class extends Error {},
    session: {
      async *query(sql, queryOptions) {
        options.push(queryOptions);
        yield* events(sql);
      },
    },
  };
  vm.runInNewContext(functions, sandbox);
  return {
    transcript,
    options,
    run: sql => sandbox.executeStatement(sql, {aborted: false}),
    clear: () => sandbox.clearOutput(),
    showWelcome() { sandbox.replaceOutput(welcome); sandbox.updateStorage(); },
    storage(mode) { sandbox.storage = mode; sandbox.updateStorage(); },
    get characters() { return sandbox.transcriptCharacters; },
  };
}

function* wideResult() {
  yield {kind: 'columns', columns: Array.from({length: 100}, (_, index) => ({name: encoder.encode(`c${index}`)}))};
  const first = Array.from({length: 100}, () => encoder.encode('x'.repeat(120)));
  const remaining = Array.from({length: 100}, () => encoder.encode('x'));
  yield {kind: 'row', values: first};
  for (let index = 1; index < 200; index++) yield {kind: 'row', values: remaining};
  yield {kind: 'complete', warnings: 0};
}

test('the actual shell bounds padded tables and still reports the full row count', async () => {
  const shell = harness(wideResult);
  await shell.run('SELECT wide_columns');
  const entry = shell.transcript.firstElementChild;
  const table = entry.children.find(child => child.className === 'result-table');
  assert.equal(table.textContent.length, 98415);
  assert.match(entry.textContent, /Display limited: 4 of 200 rows, 100 of 100 columns/);
  assert.match(entry.textContent, /200 rows in set/);
  assert.equal(shell.options[0].preview.maxRows, 500);
  assert.equal(shell.options[0].preview.maxCellBytes, 480);
});

test('transcript characters are bounded across queries and reset after clear', async () => {
  const shell = harness(wideResult);
  for (let index = 0; index < 12; index++) {
    await shell.run(`SELECT wide_${index}`);
    assert.ok(shell.transcript.textContent.length <= 1000000);
    assert.equal(shell.characters, shell.transcript.textContent.length);
  }
  assert.ok(shell.transcript.children.length < 12);
  assert.doesNotMatch(shell.transcript.textContent, /SELECT wide_0;/);
  assert.match(shell.transcript.textContent, /SELECT wide_11;/);
  shell.clear();
  assert.equal(shell.characters, 0);
  assert.equal(shell.transcript.children.length, 0);
  await shell.run('SELECT after_clear');
  assert.equal(shell.characters, shell.transcript.textContent.length);
});

test('a single statement with many result sets cannot exceed the transcript budget', async () => {
  const shell = harness(function* () {
    for (let index = 0; index < 12; index++) yield* wideResult();
  });
  await shell.run('CALL many_results()');
  assert.equal(shell.transcript.children.length, 1);
  assert.ok(shell.transcript.textContent.length <= 1000000);
  assert.equal(shell.characters, shell.transcript.textContent.length);
  assert.match(shell.transcript.firstElementChild.children.at(-1).textContent, /200 rows in set/);
});

test('storage hint changes update the budget only while the welcome is attached', () => {
  const shell = harness(wideResult);
  shell.showWelcome();
  assert.equal(shell.characters, shell.transcript.textContent.length);
  shell.storage('opfs');
  assert.equal(shell.characters, shell.transcript.textContent.length);
  assert.match(shell.transcript.textContent, /opfs/);
  shell.storage('memory');
  assert.equal(shell.characters, shell.transcript.textContent.length);
  shell.clear();
  shell.storage('opfs');
  assert.equal(shell.characters, 0);
  assert.equal(shell.transcript.textContent, '');
});

test('preview totals and notices preserve empty strings, NULL, and precise numbers', async () => {
  const shell = harness(function* () {
    yield {kind: 'columns', columns: [{name: encoder.encode('value')}]};
    for (const value of ['', null, '1234567890123456.78']) {
      yield {kind: 'row', values: [value === null ? null : encoder.encode(value)]};
    }
    yield {kind: 'complete', preview: {rowCount: 10000n, displayedRows: 3, limited: true, truncated: true}};
  });
  await shell.run('SELECT precise_values');
  assert.match(shell.transcript.textContent, /3 of 10000 rows/);
  assert.match(shell.transcript.textContent, /10000 rows in set/);
  assert.match(shell.transcript.textContent, /1234567890123456\.78/);
  assert.match(shell.transcript.textContent, /NULL/);
  assert.match(shell.transcript.textContent, /''/);
  assert.match(shell.transcript.textContent, /Long values/);
});

test('UTF8 preview prefixes and surrogate boundaries cannot introduce replacement characters', async () => {
  const shell = harness(function* () {
    yield {kind: 'columns', columns: [{name: encoder.encode('value')}]};
    yield {kind: 'row', values: [encoder.encode('a' + '中'.repeat(200)).subarray(0, 480)]};
    yield {kind: 'row', values: [encoder.encode('x'.repeat(119) + '😀tail')]};
    yield {kind: 'complete', preview: {rowCount: 2n, displayedRows: 2, limited: false, truncated: true}};
  });
  await shell.run('SELECT unicode_values');
  const text = shell.transcript.textContent;
  assert.doesNotMatch(text, /\ufffd/u);
  assert.ok(text.isWellFormed());
  assert.ok(text.includes('a' + '中'.repeat(119) + '…'));
  assert.ok(text.includes('x'.repeat(119) + '…'));
});

test('long SQL display and error messages remain within the transcript budget', async () => {
  const shell = harness(function* () { throw new Error('failure '.repeat(200000)); });
  await assert.rejects(shell.run(`SELECT '${'x'.repeat(2000000)}'`), /failure/);
  assert.ok(shell.transcript.textContent.length < 201000);
  assert.equal(shell.characters, shell.transcript.textContent.length);
  assert.match(shell.transcript.textContent, /…/);
});
