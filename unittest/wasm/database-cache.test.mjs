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
import {test} from 'node:test';
import {Database} from '../../src/wasm/database.mjs';

const compiledModule = new WebAssembly.Module(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));

class WorkerFixture extends EventTarget {
  requests = [];
  terminated = false;
  constructor({fail = false} = {}) { super(); this.fail = fail; }
  postMessage(request) {
    this.requests.push(request);
    queueMicrotask(() => this.dispatchEvent(new MessageEvent('message', {data: {
      id: request.id,
      ...(this.fail ? {error: {name: 'Error', message: 'startup failed'}}
        : {value: request.op === 'open' && request.wasmURL ? {compiledModule} : undefined}),
    }})));
  }
  terminate() { this.terminated = true; }
}

test('module cache is scoped to the loader and Wasm URL and retains only the latest module', async () => {
  const workers = [];
  const moduleURL = 'https://example.test/releases/a/engine.mjs';
  async function open(loader = moduleURL, wasmURL = 'engine.wasm') {
    const worker = new WorkerFixture();
    const db = await Database.open({moduleURL: loader, wasmURL, workerFactory: () => worker});
    workers.push(worker);
    await db.close();
    assert.equal(worker.terminated, true);
    return worker.requests[0];
  }
  assert.equal((await open()).compiledModule, undefined);
  assert.equal((await open()).compiledModule, compiledModule);
  assert.equal((await open('https://example.test/releases/b/engine.mjs')).compiledModule, undefined);
  assert.equal((await open()).compiledModule, undefined);
  assert.equal((await open(moduleURL, 'different.wasm')).compiledModule, undefined);
  assert.equal((await open(moduleURL, 'different.wasm')).compiledModule, compiledModule);
  assert.equal(workers.length, 6);
});

test('failed opens terminate their Worker and do not cache an incomplete result', async () => {
  const options = {moduleURL: 'https://example.test/failing/engine.mjs', wasmURL: 'engine.wasm'};
  const failed = new WorkerFixture({fail: true});
  await assert.rejects(Database.open({...options, workerFactory: () => failed}), /startup failed/);
  assert.equal(failed.terminated, true);
  const next = new WorkerFixture();
  const db = await Database.open({...options, workerFactory: () => next});
  assert.equal(next.requests[0].compiledModule, undefined);
  await db.close();
});

test('omitting wasmURL preserves the default loader path', async () => {
  const worker = new WorkerFixture();
  const db = await Database.open({moduleURL: 'https://example.test/default/engine.mjs', workerFactory: () => worker});
  assert.equal(worker.requests[0].wasmURL, undefined);
  assert.equal(worker.requests[0].compiledModule, undefined);
  await db.close();
});
