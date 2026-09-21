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
import {createServer} from 'node:http';
import {test} from 'node:test';
import {openRuntime} from '../../src/wasm/runtime-host.mjs';

const wasm = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]);

async function withServer(handler, run) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try { await run(`http://127.0.0.1:${server.address().port}/engine.wasm`); }
  finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

function factory(instances) {
  return async options => {
    if (options.instantiateWasm) {
      await new Promise(resolve => options.instantiateWasm({}, (instance, module) => {
        assert.ok(instance instanceof WebAssembly.Instance);
        assert.ok(module instanceof WebAssembly.Module);
        instances.push(instance);
        resolve();
      }));
    }
    return {_seekdb_runtime_state: () => 1, _seekdb_runtime_close: () => options.onExit(0)};
  };
}

test('compiled modules create fresh instances without fetching again', {timeout: 5000}, async () => {
  let requests = 0;
  await withServer((request, response) => {
    requests++;
    response.writeHead(200, {'Content-Type': 'application/wasm'});
    response.end(wasm);
  }, async wasmURL => {
    let compiledModule;
    const instances = [];
    const first = await openRuntime(factory(instances), {wasmURL,
      onCompiledModule: module => { compiledModule = module; }});
    await first.close();
    assert.ok(compiledModule instanceof WebAssembly.Module);
    const second = await openRuntime(factory(instances), {wasmURL, compiledModule});
    await second.close();
    assert.equal(requests, 1);
    assert.equal(instances.length, 2);
    assert.notEqual(instances[0], instances[1]);
  });
});

test('non-streaming MIME types still load valid Wasm', {timeout: 5000}, async () => {
  await withServer((request, response) => {
    response.writeHead(200, {'Content-Type': 'application/octet-stream'});
    response.end(wasm);
  }, async wasmURL => {
    const runtime = await openRuntime(factory([]), {wasmURL});
    await runtime.close();
  });
});

test('HTTP and compilation failures reject startup instead of waiting for the factory', {timeout: 5000}, async () => {
  await withServer((request, response) => {
    response.writeHead(404);
    response.end();
  }, async wasmURL => {
    await assert.rejects(openRuntime(factory([]), {wasmURL}), /HTTP 404/);
  });
  await withServer((request, response) => {
    response.writeHead(200, {'Content-Type': 'application/wasm'});
    response.end('invalid wasm');
  }, async wasmURL => {
    await assert.rejects(openRuntime(factory([]), {wasmURL}), WebAssembly.CompileError);
  });
});

test('the default factory path does not require a separate Wasm URL', async () => {
  let injected = true;
  const runtime = await openRuntime(options => {
    injected = 'instantiateWasm' in options;
    return factory([])(options);
  });
  assert.equal(injected, false);
  await runtime.close();
});
