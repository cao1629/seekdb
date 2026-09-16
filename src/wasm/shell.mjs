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
const welcome = transcript.querySelector('.welcome');
const terminalScroll = element('terminal-scroll');
const exampleButtons = element('example-menu').querySelectorAll('button');
const menus = document.querySelectorAll('.menu');
const versionLabel = element('version-label');
const storageHint = element('storage-hint');
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
let persistentClearPending = false;

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
  input.placeholder = next === 'loading' ? 'loading…' : next === 'closed' || next === 'error' ? 'Choose New Instance to start a database' : 'SELECT VERSION();';
  for (const button of exampleButtons) button.disabled = next !== 'ready';
  element('database-name').textContent = database ? `seekdb [${currentDatabase ?? '(none)'}]` : 'seekdb';
  element('prompt').textContent = `${element('database-name').textContent}>`;
  element('status-dot').title = message;
  updateStorage();
  if (['loading', 'running', 'closing'].includes(next)) {
    if (activityTimer === undefined) {
      operationStarted = performance.now();
      element('activity-time').textContent = elapsed(operationStarted);
      activityTimer = setInterval(() => { element('activity-time').textContent = elapsed(operationStarted); }, 100);
    }
  } else {
    clearInterval(activityTimer);
    activityTimer = undefined;
    element('activity-time').textContent = '';
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
        notice(`The database stopped: ${reconnectError.message}\nChoose New Instance to start again.`, true);
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
    const hint = storage !== 'opfs' ? 'Check that the compiled .mjs and .wasm files are available, then choose New Instance.'
      : /another tab/.test(error.message) ? 'Close it there first, or choose Memory from New Instance.'
      : /locked/.test(error.message) ? 'Another page may still be using the stored database. Close it there, then choose New Instance.'
      : 'Choose New Instance to clear the database and try again.';
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
    notice(persistent ? 'Database closed. Its data stays in this browser.' : 'Database closed. Its in-memory data was discarded.');
  } catch (error) {
    notice(`Database closed with an error: ${error.message}`, true);
  } finally {
    database = undefined;
    session = undefined;
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
  element('memory-button').disabled = busy;
  element('opfs-button').disabled = busy || !persistentSupported;
}

function closeMenus() {
  for (const menu of menus) menu.open = false;
}

async function createInstance(mode) {
  closeMenus();
  if (['loading', 'running', 'closing'].includes(state)) return;
  persistentClearPending ||= mode === 'opfs' || Boolean(database && storage === 'opfs');
  if (database) await closeDatabase();
  setState('loading', 'Clearing data and creating a new instance…');
  try {
    if (persistentClearPending) {
      const {Database} = await import('./database.mjs');
      await Database.clearPersistentStorage();
      persistentClearPending = false;
    }
    storage = mode;
    rememberStorage(mode);
    transcript.replaceChildren(welcome);
    followOutput = true;
    setInput('');
    await openDatabase();
  } catch (error) {
    versionLabel.textContent = 'Version unavailable';
    versionLabel.removeAttribute('title');
    notice(`Could not clear the stored database: ${error.message}\nClose any other page using this database, then try New Instance again.`, true);
    setState('error', 'Could not create a new instance');
  }
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
  if (event.key === 'Enter' && !event.shiftKey && (event.ctrlKey || event.metaKey || isComplete(input.value, sqlOptions) || /^\\(?:clear|tables|databases);?$/.test(input.value.trim()))) {
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
  if (event.isComposing) return;
  if (event.ctrlKey && event.key.toLowerCase() === 'l') { event.preventDefault(); clearOutput(); }
  if (state === 'running' && (event.key === 'Escape' || event.ctrlKey && event.key.toLowerCase() === 'c' && !window.getSelection().toString())) {
    event.preventDefault();
    cancelQuery();
  }
});
element('query-form').addEventListener('submit', event => { event.preventDefault(); void submit(); });
for (const button of exampleButtons) {
  button.addEventListener('click', () => {
    closeMenus();
    void submit(EXAMPLES[button.dataset.example]);
  });
}
element('clear-button').addEventListener('click', clearOutput);
element('memory-button').addEventListener('click', () => void createInstance('memory'));
element('opfs-button').addEventListener('click', () => void createInstance('opfs'));
document.addEventListener('click', event => {
  for (const menu of menus) if (!menu.contains(event.target)) menu.open = false;
});
window.addEventListener('pagehide', () => { void database?.close().catch(() => {}); });

if (storage === 'opfs' && !persistentSupported) {
  storage = 'memory';
  notice('Persistent storage is not available in this browser; the database runs in memory.');
}
if (await openDatabase() && new URLSearchParams(location.search).get('run') === 'example') {
  await submit(EXAMPLES.hybrid);
}
