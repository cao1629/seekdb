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
import {EventEmitter} from 'node:events';
import {test} from 'node:test';
import {Database, SqlError} from '../../src/wasm/database.mjs';
import {installWorkerServer} from '../../src/wasm/worker-server.mjs';

const encode = value => new TextEncoder().encode(value);
const decode = value => value === null ? null : new TextDecoder('utf-8', {fatal: true}).decode(value);
const columns = count => ({kind: 'columns', columns: Array.from({length: count}, (_, index) => ({name: encode(`c${index}`)}))});
const row = (...values) => ({kind: 'row', values: values.map(value => typeof value === 'string' ? encode(value) : value)});
const complete = () => ({kind: 'complete', warnings: 0, status: 2, moreResults: false});
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function waitFor(promise, signal) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, {once: true});
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function harness(t, query) {
  const requests = [];
  const responses = [];
  const disconnected = new Set();
  let activeReads = 0;
  let maximumReads = 0;
  let reads = 0;
  const runtime = {
    async connect() {
      const controller = new AbortController();
      const client = {
        controller,
        query(sql, {signal}) {
          const iterator = query(sql, AbortSignal.any([signal, controller.signal]));
          return {
            async next() {
              reads++;
              maximumReads = Math.max(maximumReads, ++activeReads);
              try { return await iterator.next(); }
              finally { activeReads--; }
            },
            return: () => iterator.return(),
          };
        },
      };
      return client;
    },
    disconnect(client) {
      disconnected.add(client);
      client.controller.abort(new Error('Connection closed'));
    },
    async close() {},
  };
  class TestWorker extends EventEmitter {
    constructor() {
      super();
      installWorkerServer({open: async () => runtime,
        listen: listener => { this.listener = listener; },
        send: response => {
          const copy = structuredClone(response);
          responses.push(copy);
          setImmediate(() => this.emit('message', copy));
        }});
    }
    postMessage(message) {
      requests.push(message);
      setImmediate(() => this.listener(structuredClone(message)));
    }
    async terminate() { this.emit('exit', 0); }
  }
  const worker = new TestWorker();
  const database = await Database.open({moduleURL: 'data:text/javascript,export default null', workerFactory: () => worker});
  t.after(() => database.close());
  const session = await database.connect();
  return {database, session, worker, requests, responses, disconnected,
    get reads() { return reads; }, get maximumReads() { return maximumReads; }};
}

async function collect(session, options, sql = 'query') {
  const events = [];
  for await (const event of session.query(sql, options)) events.push(event);
  return events;
}

test('bounded batches preserve public events, DECIMAL digits, binary fields and BigInt counters', async t => {
  const values = ['1234567890123456.78', '18446744073709551615', null, new Uint8Array([0, 255, 128])];
  const h = await harness(t, async function* () {
    yield columns(4);
    for (let index = 0; index < 160; ++index) yield row(...values);
    yield {...complete(), affectedRows: 9007199254740993n, lastInsertId: 18446744073709551615n};
  });
  const events = await collect(h.session);
  assert.equal(events.length, 162);
  for (const event of events.slice(1, -1)) assert.deepEqual(event.values, row(...values).values);
  assert.equal(events.at(-1).affectedRows, 9007199254740993n);
  assert.equal(events.at(-1).lastInsertId, 18446744073709551615n);
  assert.equal(events.at(-1).preview, undefined);
  const batches = h.responses.flatMap(response => response.value?.events ? [response.value.events] : []);
  assert.ok(batches.some(batch => batch.length > 1));
  assert.ok(batches.every(batch => batch.length <= 64));
  assert.equal(h.maximumReads, 1);
});

test('byte bounds carry an already-read row into the next batch without losing or duplicating it', async t => {
  const values = [160000, 160001, 300000, 17].map(size => new Uint8Array(size).fill(size % 251));
  const h = await harness(t, async function* () {
    yield columns(1);
    for (const value of values) yield row(value);
    yield complete();
  });
  const events = await collect(h.session);
  assert.deepEqual(events.filter(event => event.kind === 'row').map(event => event.values[0]), values);
  for (const {value} of h.responses) {
    if (!value?.events) continue;
    const rows = value.events.filter(event => event.kind === 'row');
    const bytes = rows.reduce((sum, event) => sum + event.values[0].length, 0);
    assert.ok(bytes <= 256 * 1024 || rows.length === 1);
  }
});

test('a slow next row cannot hold available rows or cause concurrent iterator reads', {timeout: 3000}, async t => {
  const gate = deferred();
  const entered = deferred();
  const h = await harness(t, async function* (_, signal) {
    yield row('first');
    entered.resolve();
    await waitFor(gate.promise, signal);
    yield row('second');
    yield complete();
  });
  const iterator = h.session.query('query');
  assert.equal(decode((await iterator.next()).value.values[0]), 'first');
  await entered.promise;
  const second = iterator.next();
  await nextTurn();
  await nextTurn();
  assert.equal(h.reads, 2);
  gate.resolve();
  assert.equal(decode((await second).value.values[0]), 'second');
  while (!(await iterator.next()).done) {}
  assert.equal(h.maximumReads, 1);
});

test('SQL errors follow buffered rows and leave the session reusable', async t => {
  const h = await harness(t, async function* (sql) {
    yield columns(1);
    yield row('first');
    yield row('second');
    if (sql === 'error') throw new SqlError(1064, '42000', 'Expected failure');
    yield complete();
  });
  const events = [];
  await assert.rejects(async () => {
    for await (const event of h.session.query('error')) events.push(event);
  }, error => error instanceof SqlError && error.code === 1064);
  assert.deepEqual(events.filter(event => event.kind === 'row').map(event => decode(event.values[0])), ['first', 'second']);
  assert.equal((await collect(h.session)).at(-1).kind, 'complete');
  assert.equal(h.disconnected.size, 0);
});

test('preview preserves NULL and empty values, cuts UTF8 safely and resets counters for each result', async t => {
  const h = await harness(t, async function* () {
    yield columns(4);
    for (let index = 0; index < 5; ++index) yield row(null, '', '中文😀tail', 'omitted');
    yield {...complete(), moreResults: true, warnings: 2, status: 10};
    yield columns(1);
    yield row('ok');
    yield complete();
  });
  const events = await collect(h.session, {preview: {maxRows: 2, maxColumns: 3, maxCellBytes: 7}});
  const rows = events.filter(event => event.kind === 'row');
  assert.deepEqual(rows.map(event => event.values.map(decode)), [[null, '', '中文'], [null, '', '中文'], ['ok']]);
  const results = events.filter(event => event.kind === 'complete');
  assert.deepEqual(results[0].preview, {rowCount: 5n, displayedRows: 2, limited: true, truncated: true});
  assert.equal(results[0].warnings, 2);
  assert.equal(results[0].moreResults, true);
  assert.deepEqual(results[1].preview, {rowCount: 1n, displayedRows: 1, limited: false, truncated: false});
  assert.equal(events[0].columns.length, 4);
});

for (const [name, preview, displayedRows] of [
  ['bytes', {maxBytes: 4}, 1],
  ['cells', {maxCells: 1}, 1],
  ['zero rows', {maxRows: 0}, 0],
]) {
  test(`preview ${name} limit drains the full result`, async t => {
    let produced = 0;
    const h = await harness(t, async function* () {
      yield columns(1);
      for (; produced < 3000; ++produced) yield row('abc');
      yield complete();
    });
    const events = await collect(h.session, {preview});
    assert.equal(produced, 3000);
    assert.equal(events.filter(event => event.kind === 'row').length, displayedRows);
    assert.deepEqual(events.at(-1).preview, {rowCount: 3000n, displayedRows, limited: true, truncated: false});
    assert.ok(h.responses.some(response => response.value?.events?.length === 0 && !response.value.done));
  });
}

test('preview still reports an error after omitted rows and does not alter subsequent full queries', async t => {
  let produced = 0;
  const h = await harness(t, async function* (sql) {
    yield columns(1);
    for (let index = 0; index < 100; ++index) { produced++; yield row(String(index)); }
    if (sql === 'error') throw new SqlError(1064, '42000', 'Expected failure after omitted rows');
    yield complete();
  });
  const events = [];
  await assert.rejects(async () => {
    for await (const event of h.session.query('error', {preview: {maxRows: 2}})) events.push(event);
  }, error => error instanceof SqlError && error.code === 1064);
  assert.equal(produced, 100);
  assert.equal(events.filter(event => event.kind === 'row').length, 2);
  assert.equal((await collect(h.session)).filter(event => event.kind === 'row').length, 100);
});

for (const operation of ['cancel', 'session close', 'database close']) {
  test(`${operation} wakes a pending prefetched read`, {timeout: 3000}, async t => {
    const gate = deferred();
    const entered = deferred();
    const h = await harness(t, async function* (_, signal) {
      yield row('first');
      entered.resolve();
      await waitFor(gate.promise, signal);
      yield row('never');
    });
    const controller = new AbortController();
    const iterator = h.session.query('query', {signal: controller.signal});
    assert.equal(decode((await iterator.next()).value.values[0]), 'first');
    await entered.promise;
    const pending = iterator.next();
    const rejected = assert.rejects(pending, error => operation === 'cancel'
      ? error.name === 'AbortError' : error.name === 'AbortError' || /closed|not open/.test(error.message));
    if (operation === 'cancel') controller.abort();
    else if (operation === 'session close') await h.session.close();
    else await h.database.close();
    await rejected;
    assert.equal(h.maximumReads, 1);
    assert.equal(h.disconnected.size, 1);
  });
}

test('closing a session also stops rows already buffered on the main thread', async t => {
  const h = await harness(t, async function* () {
    for (let index = 0; index < 10; ++index) yield row(String(index));
  });
  const iterator = h.session.query('query');
  assert.equal((await iterator.next()).done, false);
  await h.session.close();
  await assert.rejects(iterator.next(), /closed/);
});

test('cancellation remains observable while preview drains omitted rows', {timeout: 3000}, async t => {
  const entered = deferred();
  const gate = deferred();
  let produced = 0;
  const h = await harness(t, async function* (_, signal) {
    yield columns(1);
    for (; produced < 2000; ++produced) yield row('omitted');
    entered.resolve();
    await waitFor(gate.promise, signal);
    yield complete();
  });
  const controller = new AbortController();
  const pending = collect(h.session, {signal: controller.signal, preview: {maxRows: 0}});
  const rejected = assert.rejects(pending, {name: 'AbortError'});
  await entered.promise;
  controller.abort();
  await rejected;
  assert.equal(produced, 2000);
  assert.equal(h.maximumReads, 1);
  assert.equal(h.disconnected.size, 1);
});

test('invalid preview options fail before starting SQL and leave the session reusable', async t => {
  let started = 0;
  const h = await harness(t, async function* () { started++; yield complete(); });
  for (const preview of [null, [], {unknown: 1}, {maxRows: -1}, {maxBytes: Infinity}, {maxColumns: 0}]) {
    await assert.rejects(collect(h.session, {preview}), /preview/);
  }
  assert.equal(started, 0);
  assert.equal((await collect(h.session)).length, 1);
});
