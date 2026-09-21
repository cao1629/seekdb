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

import net from 'node:net';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

function parseOptions(argv) {
  const options = {startupTimeoutMs: 120000, shutdownTimeoutMs: 60000};
  const names = new Map([
    ['--module', 'modulePath'], ['--port', 'port'],
    ['--startup-timeout-ms', 'startupTimeoutMs'],
    ['--shutdown-timeout-ms', 'shutdownTimeoutMs'],
  ]);
  for (let index = 0; index < argv.length; index++) {
    if (argv[index] === '--help') {
      console.log('Usage: node tools/wasm/mysqltest-bridge.mjs --module /path/to/seekdb_wasm_database.mjs --port PORT [--startup-timeout-ms 120000] [--shutdown-timeout-ms 60000]');
      process.exit(0);
    }
    const name = names.get(argv[index]);
    if (!name || index + 1 >= argv.length) throw new Error(`Invalid option: ${argv[index]}`);
    options[name] = argv[++index];
  }
  if (!options.modulePath || !options.port) throw new Error('--module and --port are required');
  for (const name of ['port', 'startupTimeoutMs', 'shutdownTimeoutMs']) {
    options[name] = Number(options[name]);
    const maximum = name === 'port' ? 65535 : 2147483647;
    if (!Number.isInteger(options[name]) || options[name] < 1 || options[name] > maximum) {
      throw new Error(`Invalid ${name}`);
    }
  }
  options.moduleURL = pathToFileURL(resolve(options.modulePath));
  return options;
}

let options;
try {
  options = parseOptions(process.argv.slice(2));
} catch (error) {
  console.error(`wasm-tcp-bridge: ${error.message}`);
  process.exit(2);
}

let module;
let server;
let startupTask;
let startupTimer;
let shutdownTask;
let shuttingDown = false;
let runtimeTerminated = false;
let exitCode = 0;
let finishRuntime;
const runtimeExited = new Promise(resolve => { finishRuntime = resolve; });
const peers = new Set();

function closePeer(peer) {
  if (peer.closed) return;
  peer.closed = true;
  peers.delete(peer);
  peer.socket.destroy();
  if (!runtimeTerminated) peer.transport.close();
}

function shutdown(code, reason) {
  exitCode = Math.max(exitCode, code);
  if (reason) console.error(`wasm-tcp-bridge: ${reason}`);
  if (shutdownTask) return shutdownTask;
  shuttingDown = true;
  clearTimeout(startupTimer);
  shutdownTask = (async () => {
    const deadline = setTimeout(() => {
      console.error('wasm-tcp-bridge: shutdown timed out');
      process.exit(exitCode || 1);
    }, options.shutdownTimeoutMs);
    try {
      const serverClosed = server?.listening
        ? new Promise(resolve => server.close(resolve)) : Promise.resolve();
      for (const peer of [...peers]) closePeer(peer);
      if (!module && !runtimeTerminated) await startupTask?.catch(() => {});
      if (module && !runtimeTerminated) {
        module._seekdb_runtime_set_external_tcp_port(0);
        module._seekdb_runtime_close();
        await runtimeExited;
      }
      await serverClosed;
    } catch (error) {
      exitCode = Math.max(exitCode, 1);
      console.error(`wasm-tcp-bridge: shutdown failed: ${error.stack ?? error}`);
    }
    clearTimeout(deadline);
    console.log(`wasm-tcp-bridge: stopped exit=${exitCode}`);
    process.exit(exitCode);
  })();
  return shutdownTask;
}

function runtimeEnded(code, error) {
  if (runtimeTerminated) return;
  runtimeTerminated = true;
  finishRuntime();
  if (error || code !== 0) {
    void shutdown(1, error?.stack ?? `runtime exited with code ${code}`);
  } else if (!shuttingDown) {
    void shutdown(1, 'runtime exited unexpectedly');
  }
}

function waitForDrain(socket) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      socket.off('drain', drained);
      socket.off('close', closed);
      socket.off('error', failed);
    };
    const drained = () => { cleanup(); resolve(); };
    const closed = () => { cleanup(); reject(new Error('TCP connection closed')); };
    const failed = error => { cleanup(); reject(error); };
    socket.once('drain', drained);
    socket.once('close', closed);
    socket.once('error', failed);
    if (socket.destroyed) closed();
  });
}

function acceptSocket(socket, WasmMemoryTransport) {
  if (shuttingDown || runtimeTerminated) { socket.destroy(); return; }
  let transport;
  try {
    transport = new WasmMemoryTransport(module, module._seekdb_runtime_connect(65536));
  } catch (error) {
    console.error(`wasm-tcp-bridge: connection admission failed: ${error.message}`);
    socket.destroy();
    return;
  }
  const peer = {socket, transport, closed: false};
  peers.add(peer);
  socket.setNoDelay(true);
  socket.on('error', () => closePeer(peer));
  socket.on('close', () => closePeer(peer));
  const incoming = async () => {
    for await (const bytes of socket) {
      if (peer.closed) return;
      await transport.write(bytes);
    }
  };
  const outgoing = async () => {
    while (!peer.closed) {
      const bytes = await transport.read();
      if (peer.closed) return;
      if (bytes === null) {
        await new Promise(resolve => socket.end(resolve));
        return;
      }
      if (!socket.write(bytes)) await waitForDrain(socket);
    }
  };
  for (const task of [incoming(), outgoing()]) {
    void task.catch(error => {
      if (!peer.closed && !shuttingDown) {
        console.error(`wasm-tcp-bridge: connection closed: ${error.message}`);
      }
    }).finally(() => closePeer(peer));
  }
}

async function start() {
  const [{default: factory}, {runtimeArguments}, {WasmMemoryTransport}] = await Promise.all([
    import(options.moduleURL.href),
    import(new URL('./runtime-host.mjs', options.moduleURL).href),
    import(new URL('./mysql-transport.mjs', options.moduleURL).href),
  ]);
  if (shuttingDown) return;
  module = await factory({
    arguments: [...runtimeArguments(), 'memory'],
    onExit: code => runtimeEnded(code),
    onAbort: reason => runtimeEnded(1, new Error(`WASM runtime aborted: ${reason}`)),
  });
  while (!shuttingDown && !runtimeTerminated) {
    const state = module._seekdb_runtime_state();
    if (state === 1) break;
    if (state !== 0) throw new Error(`Database startup failed with runtime state ${state}`);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  if (shuttingDown || runtimeTerminated) return;
  server = net.createServer(socket => acceptSocket(socket, WasmMemoryTransport));
  server.on('error', error => { void shutdown(1, error.stack ?? error); });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(options.port, '127.0.0.1', resolve);
  });
  if (shuttingDown) return;
  const address = server.address();
  if (!address || typeof address === 'string'
      || module._seekdb_runtime_set_external_tcp_port(address.port) !== 0) {
    throw new Error('Cannot register the listening TCP endpoint');
  }
  clearTimeout(startupTimer);
  console.log(`wasm-tcp-bridge: ready host=127.0.0.1 port=${options.port} pid=${process.pid} module=${options.moduleURL.href}`);
}

process.on('SIGINT', () => { void shutdown(0, 'received SIGINT'); });
process.on('SIGTERM', () => { void shutdown(0, 'received SIGTERM'); });
process.on('uncaughtException', error => { void shutdown(1, error.stack ?? error); });
process.on('unhandledRejection', error => { void shutdown(1, error?.stack ?? error); });
startupTimer = setTimeout(() => {
  void shutdown(1, 'startup timed out');
}, options.startupTimeoutMs);
startupTask = start();
void startupTask.catch(error => shutdown(1, error.stack ?? error));
