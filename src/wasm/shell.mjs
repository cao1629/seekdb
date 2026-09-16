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
import {CommandHistory, databaseAfterStatement, isComplete, takeStatement} from './shell-sql.mjs';
import {EXAMPLES} from './shell-examples.mjs';
import {formatTable} from './shell-format.mjs';

const element = id => document.getElementById(id);
const input = element('sql');
const transcript = element('transcript');
const terminalScroll = element('terminal-scroll');
const connectionButton = element('connection-button');
const runButton = element('run-button');
const cancelButton = element('cancel-button');
const exampleButton = element('example-button');
const versionLabel = element('version-label');
const storageHint = element('storage-hint');
const help = element('help-dialog');
const decoder = new TextDecoder();
const history = new CommandHistory();
const decode = value => value === null ? null : decoder.decode(value);
let database;
let session;
let SqlError;
let state = 'loading';
let currentDatabase = 'oceanbase';
let sqlOptions = {};
let controller;
let operationStarted;
let activityTimer;
let followOutput = true;
let activeEntry;
const STORAGE_KEY = 'seekdb-shell-storage';
const persistentSupported = typeof navigator.storage?.getDirectory === 'function' && typeof navigator.locks?.request === 'function';
let storage = rememberedStorage();

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function elapsed(start) {
  return `${((performance.now() - start) / 1000).toFixed(3)} s`;
}

function setState(next, message) {
  state = next;
  element('status-dot').dataset.state = next;
  element('status-text').textContent = message;
  input.disabled = !['ready', 'running'].includes(next);
  input.readOnly = next === 'running';
  input.placeholder = next === 'loading' ? 'loading…' : next === 'closed' || next === 'error' ? 'Connect to start a database' : 'SELECT VERSION();';
  runButton.disabled = next !== 'ready' || !input.value.trim();
  runButton.hidden = next === 'running';
  cancelButton.hidden = next !== 'running';
  cancelButton.disabled = false;
  exampleButton.disabled = next !== 'ready';
  element('example').disabled = !['ready', 'closed', 'error'].includes(next);
  connectionButton.disabled = ['loading', 'running', 'closing'].includes(next);
  connectionButton.textContent = next === 'loading' ? 'Starting…' : next === 'closing' ? 'Closing…' : database ? 'Close database' : 'Connect';
  connectionButton.title = storage === 'opfs'
    ? database ? 'Close the database; its data stays in this browser' : 'Open the database stored in this browser'
    : database ? 'Close the database and discard its in-memory data' : 'Start a new in-memory database';
  element('database-name').textContent = database ? `seekdb [${currentDatabase ?? '(none)'}]` : 'seekdb';
  element('prompt').textContent = `${element('database-name').textContent}>`;
  element('engine-label').textContent = database ? 'seekdb · wasm' : 'WebAssembly';
  element('status-dot').title = message;
  updateStorage();
  clearInterval(activityTimer);
  element('activity-time').textContent = '';
  if (['loading', 'running', 'closing'].includes(next)) {
    operationStarted = performance.now();
    activityTimer = setInterval(() => { element('activity-time').textContent = elapsed(operationStarted); }, 100);
  }
}

function appendOutput(output) {
  transcript.append(output);
  while (transcript.children.length > 40) transcript.firstElementChild.remove();
  scrollOutput();
}

function scrollOutput() {
  if (followOutput) terminalScroll.scrollTop = terminalScroll.scrollHeight;
}

function notice(message, error = false) {
  appendOutput(node('div', `notice${error ? ' error' : ''}`, message));
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(190, Math.max(20, input.scrollHeight))}px`;
  element('query-form').dataset.empty = String(!input.value.trim());
  runButton.disabled = state !== 'ready' || !input.value.trim();
  scrollOutput();
}

function setInput(value) {
  input.value = value;
  resizeInput();
  input.focus();
  input.setSelectionRange(value.length, value.length);
}

function commandOutput(sql) {
  const entry = node('article', 'entry');
  const line = node('div', 'command-line');
  const command = node('pre', 'command-text');
  command.append(node('span', 'command-prompt', element('prompt').textContent), document.createTextNode(` ${sql.replaceAll('\n', '\n    -> ')};`));
  line.append(command);
  const reuse = node('button', 'reuse-button', 'Reuse');
  reuse.title = 'Copy this statement to the editor';
  reuse.addEventListener('click', () => { if (state === 'ready') setInput(`${sql};`); });
  line.append(reuse);
  entry.append(line);
  appendOutput(entry);
  return entry;
}

function updateContext(sql, event) {
  currentDatabase = databaseAfterStatement(sql, currentDatabase, sqlOptions);
  if ('affectedRows' in event) sqlOptions.noBackslashEscapes = Boolean(event.status & 512);
  element('database-name').textContent = `seekdb [${currentDatabase ?? '(none)'}]`;
  element('prompt').textContent = `${element('database-name').textContent}>`;
}

async function executeStatement(sql, signal) {
  const entry = commandOutput(sql);
  activeEntry = entry;
  const started = performance.now();
  let body;
  let resultColumns = [];
  let resultRows = [];
  let rows = 0n;
  let displayed = 0;
  let characters = 0;
  let limited = false;
  let textShortened = false;
  let columns = 0;
  try {
    for await (const event of session.query(sql, {signal})) {
      if (event.kind === 'columns') {
        rows = 0n;
        displayed = 0;
        characters = 0;
        columns = event.columns.length;
        limited = columns > 100;
        textShortened = false;
        resultColumns = event.columns.slice(0, 100).map(column => {
          const name = decode(column.name);
          if (name.length <= 120) return name;
          textShortened = true;
          return `${name.slice(0, 120)}…`;
        });
        resultRows = [];
        body = node('pre', 'result-table');
        body.setAttribute('aria-label', 'Query result');
        body.tabIndex = 0;
        entry.append(body);
      } else if (event.kind === 'row') {
        rows += 1n;
        if (displayed >= 500 || characters >= 100000 || displayed * Math.min(columns, 100) >= 20000) {
          limited = true;
          continue;
        }
        const row = [];
        for (const value of event.values.slice(0, 100)) {
          let text = decode(value);
          if (text !== null && text.length > 120) {
            text = `${text.slice(0, 120)}…`;
            textShortened = true;
          }
          characters += text === null ? 4 : text.length;
          row.push(text);
        }
        resultRows.push(row);
        displayed++;
        if (displayed % 50 === 0) {
          body.textContent = formatTable(resultColumns, resultRows);
          scrollOutput();
        }
      } else if (event.kind === 'complete') {
        updateContext(sql, event);
        if (body) {
          body.textContent = resultRows.length ? formatTable(resultColumns, resultRows) : '';
          body.hidden = rows === 0n;
        }
        if (limited) entry.append(node('p', 'result-limit', `Display limited: ${displayed} of ${rows} rows, ${Math.min(columns, 100)} of ${columns} columns. The full response was consumed.`));
        if (textShortened) entry.append(node('p', 'result-limit', 'Long values and column names are shortened to 120 characters for display.'));
        const summary = node('div', 'query-summary');
        const affected = event.affectedRows ?? 0n;
        const count = body ? rows === 0n ? 'Empty set' : `${rows} ${rows === 1n ? 'row' : 'rows'} in set` : `Query OK, ${affected} ${affected === 1n ? 'row' : 'rows'} affected`;
        const warnings = event.warnings ? ` · ${event.warnings} ${event.warnings === 1 ? 'warning' : 'warnings'}` : '';
        summary.append(node('span', '', `${count}${warnings}`), node('span', 'timing', `(${elapsed(started).replace(' s', ' sec')})`));
        entry.append(summary);
        body = undefined;
        scrollOutput();
      }
    }
  } catch (error) {
    if (body && resultRows.length) body.textContent = formatTable(resultColumns, resultRows);
    const message = signal.aborted ? 'Query cancelled. Remaining statements were not run.' : error instanceof SqlError ? `ERROR ${error.code} (${error.sqlState}): ${error.message}` : error.message;
    entry.append(node('div', 'entry-error', message));
    throw error;
  } finally {
    activeEntry = undefined;
    scrollOutput();
  }
}

async function reconnectSession() {
  await session?.close().catch(() => {});
  session = undefined;
  try {
    session = await database.connect(currentDatabase ? {database: currentDatabase} : undefined);
  } catch (error) {
    if (!(error instanceof SqlError) || error.code !== 1049) throw error;
    currentDatabase = 'oceanbase';
    session = await database.connect();
  }
  if (currentDatabase === null) currentDatabase = 'oceanbase';
  sqlOptions = {};
}

async function submit(sql = input.value) {
  if (state !== 'ready' || !sql.trim()) return;
  const command = sql.trim().replace(/;$/, '').toLowerCase();
  if (command === '\\clear') { clearOutput(); setInput(''); return; }
  if (command === '\\help' || command === '?') { help.showModal(); return; }
  if (command === '\\tables') sql = 'SHOW TABLES;';
  if (command === '\\databases') sql = 'SHOW DATABASES;';
  history.push(sql);
  setInput('');
  followOutput = true;
  scrollOutput();
  controller = new AbortController();
  const signal = controller.signal;
  setState('running', 'Running SQL…');
  let remaining = sql;
  let statements = 0;
  let failed = false;
  try {
    for (;;) {
      signal.throwIfAborted();
      const next = takeStatement(remaining, sqlOptions);
      if (!next) break;
      await executeStatement(next.statement, signal);
      statements++;
      remaining = next.rest;
    }
  } catch (error) {
    failed = true;
    if (error instanceof SyntaxError) notice(`${error.message}\nRemaining SQL was not run. Use ↑ to edit the original input.`, true);
    if (signal.aborted || !(error instanceof SqlError || error instanceof SyntaxError)) {
      try {
        await reconnectSession();
        notice('Session reconnected. Uncommitted changes were rolled back; session settings were reset.');
      } catch (reconnectError) {
        await database?.close().catch(() => {});
        database = undefined;
        session = undefined;
        notice(`The database stopped: ${reconnectError.message}\nConnect to start it again.`, true);
      }
    }
  } finally {
    controller = undefined;
    setState(database ? 'ready' : 'error', database ? failed ? 'Ready · previous batch stopped before completion' : `Ready · ${statements} ${statements === 1 ? 'statement' : 'statements'} completed` : 'Database unavailable');
    input.focus();
  }
}

function cancelQuery() {
  if (!controller || controller.signal.aborted) return;
  controller.abort();
  cancelButton.disabled = true;
  element('status-text').textContent = 'Cancelling query and reconnecting the session…';
}

async function updateVersion() {
  let metadata;
  try {
    metadata = await database.connect({database: 'oceanbase'});
    let engineVersion;
    for await (const event of metadata.query('SELECT VERSION()')) {
      if (event.kind === 'row') engineVersion = decode(event.values[0]);
    }
    versionLabel.textContent = engineVersion?.match(/seekdb-(v\S+)/i)?.[1] ?? engineVersion ?? 'Version unavailable';
    if (engineVersion) versionLabel.title = `WebAssembly · ${engineVersion}`;
  } catch {
    versionLabel.textContent = 'Version unavailable';
  } finally {
    await metadata?.close().catch(() => {});
  }
}

async function openDatabase() {
  versionLabel.textContent = 'Loading…';
  versionLabel.removeAttribute('title');
  setState('loading', 'Loading WebAssembly and starting seekdb. The first start can take a few seconds…');
  try {
    if (!globalThis.isSecureContext || !globalThis.crossOriginIsolated || typeof SharedArrayBuffer === 'undefined') {
      throw new Error('WebAssembly threads require a secure context with cross-origin isolation. Use tools/wasm/serve-shell.py on localhost, or serve over HTTPS with COOP: same-origin and COEP: require-corp.');
    }
    const module = await import('./database.mjs');
    SqlError = module.SqlError;
    database = await module.Database.open({moduleURL: new URL('./seekdb_wasm_database.mjs', import.meta.url), storage});
    await updateVersion();
    currentDatabase = 'oceanbase';
    session = await database.connect({database: currentDatabase});
    sqlOptions = {};
    setState('ready', storage === 'opfs' ? 'Ready · root · data is kept in this browser' : 'Ready · root · SQL runs locally in a Web Worker');
    input.focus();
    return true;
  } catch (error) {
    await database?.close().catch(() => {});
    database = undefined;
    session = undefined;
    versionLabel.textContent = 'Version unavailable';
    const hint = storage !== 'opfs' ? 'Check that the compiled .mjs and .wasm files are available, then try Connect.'
      : /another tab/.test(error.message) ? 'Close it there first, or choose In memory from the Storage menu.'
      : /locked/.test(error.message) ? 'Another page may still be using the stored database. Close it there, then try Connect again.'
      : 'Try Connect again. If it keeps failing, choose Delete stored data from the Storage menu to start over, or switch to In memory.';
    notice(`Could not start seekdb: ${error.message}\n${hint}`, true);
    setState('error', 'Startup failed');
    return false;
  }
}

async function closeDatabase() {
  const persistent = storage === 'opfs';
  setState('closing', persistent ? 'Closing the database…' : 'Closing the in-memory database…');
  try {
    await database.close();
    notice(persistent ? 'Database closed. Its data stays in this browser. Connect reopens it.' : 'Database closed. Its in-memory data was discarded. Connect to start a fresh database.');
  } catch (error) {
    notice(`Database closed with an error: ${error.message}`, true);
  } finally {
    database = undefined;
    session = undefined;
    setState('closed', persistent ? 'Database closed · Connect reopens the stored database' : 'Database closed · Connect starts an empty database');
  }
}

function rememberedStorage() {
  const fallback = persistentSupported ? 'opfs' : 'memory';
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === 'opfs' || saved === 'memory' ? saved : fallback;
  } catch { return fallback; }
}

function rememberStorage(mode) {
  try { localStorage.setItem(STORAGE_KEY, mode); } catch {}
}

function updateStorage() {
  const persistent = storage === 'opfs';
  const busy = ['loading', 'running', 'closing'].includes(state);
  element('storage-badge').textContent = persistent ? 'opfs://' : 'memory://';
  storageHint.textContent = persistent ? 'Storage: opfs:// — data is kept in this browser.' : 'Storage: memory:// — data clears on reload or close.';
  element('memory-button').setAttribute('aria-pressed', String(!persistent));
  element('opfs-button').setAttribute('aria-pressed', String(persistent));
  element('memory-button').disabled = busy;
  element('opfs-button').disabled = busy || !persistentSupported;
  element('wipe-button').disabled = busy || !persistentSupported;
}

function closeMenu() {
  element('storage-menu').open = false;
}

async function switchStorage(mode) {
  closeMenu();
  if (mode === storage || ['loading', 'running', 'closing'].includes(state)) return;
  if (database && storage === 'memory' && !confirm('Switch to storage in this browser? The current in-memory data is discarded.')) return;
  if (database) await closeDatabase();
  storage = mode;
  rememberStorage(mode);
  await openDatabase();
}

async function wipeStorage() {
  closeMenu();
  if (['loading', 'running', 'closing'].includes(state)) return;
  if (!confirm('Delete the database stored in this browser? This cannot be undone.')) return;
  if (database && storage === 'opfs') await closeDatabase();
  setState('loading', 'Deleting stored data…');
  let deleted = false;
  try {
    const module = await import('./database.mjs');
    await module.Database.clearPersistentStorage();
    deleted = true;
    notice('Stored data deleted.');
  } catch (error) {
    notice(`Could not delete stored data: ${error.message}`, true);
  }
  if (storage === 'opfs') await openDatabase();
  else setState(database ? 'ready' : 'closed', database ? deleted ? 'Ready · stored data deleted' : 'Ready · stored data deletion failed' : 'Database closed · Connect starts an empty database');
}

function clearOutput() {
  transcript.replaceChildren(...(activeEntry ? [activeEntry] : []));
  followOutput = true;
  scrollOutput();
  if (state === 'ready') input.focus();
}

input.addEventListener('input', () => {
  history.resetNavigation();
  resizeInput();
});
terminalScroll.addEventListener('scroll', () => {
  followOutput = terminalScroll.scrollHeight - terminalScroll.scrollTop - terminalScroll.clientHeight < 60;
});
input.addEventListener('keydown', event => {
  if (event.isComposing) return;
  if (state !== 'ready') return;
  if (event.key === 'Enter' && !event.shiftKey && (event.ctrlKey || event.metaKey || isComplete(input.value, sqlOptions) || /^\\(?:help|clear|tables|databases);?$/.test(input.value.trim()) || input.value.trim() === '?')) {
    event.preventDefault();
    void submit();
  } else if (event.key === 'ArrowUp' && !input.value.slice(0, input.selectionStart).includes('\n') && input.selectionStart === input.selectionEnd) {
    const previous = history.previous(input.value);
    if (previous !== undefined) { event.preventDefault(); setInput(previous); }
  } else if (event.key === 'ArrowDown' && !input.value.slice(input.selectionEnd).includes('\n') && input.selectionStart === input.selectionEnd) {
    const next = history.next();
    if (next !== undefined) { event.preventDefault(); setInput(next); }
  }
});
document.addEventListener('keydown', event => {
  if (help.open || event.isComposing) return;
  if (event.ctrlKey && event.key.toLowerCase() === 'l') { event.preventDefault(); clearOutput(); }
  if (state === 'running' && (event.key === 'Escape' || event.ctrlKey && event.key.toLowerCase() === 'c' && !window.getSelection().toString())) {
    event.preventDefault();
    cancelQuery();
  }
});
element('query-form').addEventListener('submit', event => { event.preventDefault(); void submit(); });
cancelButton.addEventListener('click', cancelQuery);
exampleButton.addEventListener('click', () => void submit(EXAMPLES[element('example').value]));
element('clear-button').addEventListener('click', clearOutput);
connectionButton.addEventListener('click', () => { if (database) void closeDatabase(); else void openDatabase(); });
element('help-button').addEventListener('click', () => help.showModal());
element('help-close').addEventListener('click', () => help.close());
help.addEventListener('close', () => input.focus());
element('memory-button').addEventListener('click', () => void switchStorage('memory'));
element('opfs-button').addEventListener('click', () => void switchStorage('opfs'));
element('wipe-button').addEventListener('click', () => void wipeStorage());
document.addEventListener('click', event => { if (!element('storage-menu').contains(event.target)) closeMenu(); });
window.addEventListener('pagehide', () => { void database?.close().catch(() => {}); });

if (storage === 'opfs' && !persistentSupported) {
  storage = 'memory';
  notice('Persistent storage is not available in this browser; the database runs in memory.');
}
if (await openDatabase() && new URLSearchParams(location.search).get('run') === 'example') {
  await submit(EXAMPLES.quickstart);
}
