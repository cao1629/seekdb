// Copyright (c) 2026 OceanBase. SPDX-License-Identifier: Apache-2.0
import {Database, SqlError} from './database.mjs';

export async function runDatabaseBrowserCases({
  open = options => Database.open({moduleURL: new URL('./seekdb_wasm_database.mjs', import.meta.url), ...options}),
  report = console.log,
} = {}) {
  const equal = (actual, expected) => {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error(`Expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`);
    }
  };
  const rejects = async (promise, match) => {
    try { await promise; }
    catch (error) { if (match(error)) return; throw error; }
    throw new Error('Expected operation to reject');
  };
  const decoder = new TextDecoder();
  async function query(session, sql, options) {
    const rows = [];
    for await (const event of session.query(sql, options)) {
      if (event.kind === 'row') rows.push(event.values.map(value => value === null ? null : decoder.decode(value)));
      if (event.kind === 'complete' && 'affectedRows' in event) equal(typeof event.affectedRows, 'bigint');
    }
    return rows;
  }
  let db;
  try {
    const start = performance.now();
    db = await open();
    report(`open: ${Math.round(performance.now() - start)} ms`);
    const session = await db.connect({capacity: 257, chunkBytes: 31});
    equal(await query(session, "SELECT CAST(18446744073709551615 AS UNSIGNED), NULL, '中文'"),
      [['18446744073709551615', null, '中文']]);
    await rejects(query(session, 'SELECT FROM'), error => error instanceof SqlError && typeof error.code === 'number');
    equal(await query(session, 'SELECT 2'), [['2']]);
    await query(session, 'CREATE DATABASE browser_test');
    await query(session, 'USE browser_test');
    await query(session, 'CREATE TABLE items (id INT PRIMARY KEY, value VARCHAR(64))');
    await query(session, "INSERT INTO items VALUES (1, 'original'), (2, 'delete')");
    await query(session, 'BEGIN');
    await query(session, "UPDATE items SET value = 'rollback' WHERE id = 1");
    await query(session, 'ROLLBACK');
    equal(await query(session, 'SELECT value FROM items WHERE id = 1'), [['original']]);
    await query(session, 'BEGIN');
    await query(session, "UPDATE items SET value = 'commit' WHERE id = 1");
    await query(session, 'DELETE FROM items WHERE id = 2');
    await query(session, 'COMMIT');
    equal(await query(session, 'SELECT id, value FROM items'), [['1', 'commit']]);
    report('PASS: SQL, exact values, error recovery, CRUD, rollback and commit');
    const disconnected = await db.connect({database: 'browser_test'});
    await query(disconnected, 'BEGIN');
    await query(disconnected, "UPDATE items SET value = 'uncommitted' WHERE id = 1");
    await disconnected.close();
    await query(session, "UPDATE items SET value = 'released' WHERE id = 1");
    equal(await query(session, 'SELECT value FROM items WHERE id = 1'), [['released']]);
    const abandoned = await db.connect();
    const iterator = abandoned.query('SELECT 1 UNION ALL SELECT 2');
    equal((await iterator.next()).value.kind, 'columns');
    await iterator.return();
    await abandoned.close();
    const cancelled = await db.connect();
    const controller = new AbortController();
    const active = query(cancelled, 'SELECT SLEEP(1)', {signal: controller.signal});
    const timer = setTimeout(() => controller.abort(), 20);
    try { await rejects(active, error => error.name === 'AbortError'); }
    finally { clearTimeout(timer); }
    await cancelled.close();
    equal(await query(session, 'SELECT 3'), [['3']]);
    report('PASS: disconnect rollback, result release and cancellation preserving peers');
    await db.close();
    await db.close();
    report('PASS: native shutdown and Worker termination');
    db = await open();
    const fresh = await db.connect();
    const databases = await query(fresh, 'SHOW DATABASES');
    equal(databases.some(row => row[0] === 'browser_test'), false);
    equal(await query(fresh, 'SELECT 4'), [['4']]);
    await db.close();
    report('PASS: fresh reopen in memory');
    if (typeof navigator === 'undefined' || typeof navigator.storage?.getDirectory !== 'function') {
      report('SKIP: persistent storage needs OPFS, which is not available here');
      return;
    }
    report('Starting persistent storage checks: clearing the test origin');
    await Database.clearPersistentStorage();
    report('Starting the engine on OPFS');
    const persistentStart = performance.now();
    db = await open({storage: 'opfs'});
    report(`open with persistent storage: ${Math.round(performance.now() - persistentStart)} ms`);
    await rejects(open({storage: 'opfs'}), error => /another tab/.test(error.message));
    await rejects(Database.clearPersistentStorage(), error => /another tab/.test(error.message));
    report('PASS: a persistent database prevents a second open and clearing');
    let stored = await db.connect();
    await query(stored, 'CREATE DATABASE persistent_test');
    await query(stored, 'CREATE TABLE persistent_test.items (id INT PRIMARY KEY, value VARCHAR(64))');
    await query(stored, "INSERT INTO persistent_test.items VALUES (1, 'kept')");
    await query(stored, 'BEGIN');
    await query(stored, "UPDATE persistent_test.items SET value = 'uncommitted' WHERE id = 1");
    equal(await query(stored, 'SELECT value FROM persistent_test.items WHERE id = 1'), [['uncommitted']]);
    report('Closing the persistent database with an unfinished transaction');
    await db.close();
    report('Reopening the persistent database');
    db = await open({storage: 'opfs'});
    stored = await db.connect({database: 'persistent_test'});
    equal(await query(stored, 'SELECT id, value FROM items'), [['1', 'kept']]);
    await db.close();
    report('PASS: persistent reopen keeps committed data and rolls back unfinished transactions; one instance per origin');
    const root = await navigator.storage.getDirectory();
    const sys = await root.getDirectoryHandle('store').then(d => d.getDirectoryHandle('redo')).then(d => d.getDirectoryHandle('sys'));
    const stream = await sys.getDirectoryHandle('log_stream');
    const pending = await sys.getDirectoryHandle('log_stream.tmp', {create: true});
    const meta = await stream.getDirectoryHandle('meta');
    const pendingMeta = await pending.getDirectoryHandle('meta', {create: true});
    const blocks = [];
    for await (const name of meta.keys()) blocks.push(name);
    for (const name of blocks) await (await meta.getFileHandle(name)).move(pendingMeta, name);
    await stream.removeEntry('meta');
    const journal = await (await root.getFileHandle('.move', {create: true})).createWritable();
    await journal.write('/seekdb/store/redo/sys/log_stream.tmp\n/seekdb/store/redo/sys/log_stream\n');
    await journal.close();
    db = await open({storage: 'opfs'});
    stored = await db.connect({database: 'persistent_test'});
    equal(await query(stored, 'SELECT id, value FROM items'), [['1', 'kept']]);
    await db.close();
    const exists = async (directory, name) => {
      try { await directory.getDirectoryHandle(name); return true; } catch {}
      try { await directory.getFileHandle(name); return true; } catch { return false; }
    };
    equal([await exists(root, '.move'), await exists(sys, 'log_stream.tmp'), await exists(stream, 'meta')], [false, false, true]);
    report('PASS: an interrupted directory move is finished at the next start');
    const holderURL = URL.createObjectURL(new Blob([`
      let access;
      self.onmessage = async ({data}) => {
        try {
          if (data === 'release') {
            access.close();
            access = undefined;
            self.postMessage('released');
            return;
          }
          let directory = await navigator.storage.getDirectory();
          for (const part of data.directory) directory = await directory.getDirectoryHandle(part);
          access = await (await directory.getFileHandle(data.name)).createSyncAccessHandle();
          self.postMessage('held');
        } catch (error) {
          self.postMessage({error: {name: error.name, message: error.message}});
        }
      };`], {type: 'text/javascript'}));
    let holder;
    let clearHolderReply;
    try {
      report('Starting the file-lock helper for store/sstable/meta.db');
      holder = new Worker(holderURL);
      const holderReply = (message, stage) => new Promise((resolve, reject) => {
        const finish = (error, value) => {
          clearHolderReply();
          if (error) reject(new Error(`File-lock helper ${stage}: ${error}`));
          else resolve(value);
        };
        const timer = setTimeout(() => finish('no reply within 15 seconds'), 15000);
        clearHolderReply = () => {
          clearTimeout(timer);
          holder.onmessage = null;
          holder.onerror = null;
          holder.onmessageerror = null;
          clearHolderReply = undefined;
        };
        holder.onmessage = ({data}) => finish(data?.error ? `${data.error.name}: ${data.error.message}` : undefined, data);
        holder.onerror = event => finish(event.message || 'Worker failed');
        holder.onmessageerror = () => finish('Worker reply could not be decoded');
        try { holder.postMessage(message); }
        catch (error) { finish(error.message); }
      });
      equal(await holderReply({directory: ['store', 'sstable'], name: 'meta.db'}, 'opening store/sstable/meta.db'), 'held');
      report('File-lock helper holds meta.db; checking that the engine reports the locked file');
      await rejects(open({storage: 'opfs'}), error => /locked/.test(error.message));
      report('Locked-file rejection received; releasing the helper file handle');
      equal(await holderReply('release', 'releasing store/sstable/meta.db'), 'released');
    } finally {
      clearHolderReply?.();
      holder?.terminate();
      URL.revokeObjectURL(holderURL);
    }
    report('File-lock helper stopped; reopening the persistent database');
    db = await open({storage: 'opfs'});
    stored = await db.connect({database: 'persistent_test'});
    equal(await query(stored, 'SELECT id FROM items'), [['1']]);
    await db.close();
    report('PASS: locked files are reported instead of a failed start');
    await Database.clearPersistentStorage();
    db = await open({storage: 'opfs'});
    stored = await db.connect();
    equal((await query(stored, 'SHOW DATABASES')).some(row => row[0] === 'persistent_test'), false);
    await db.close();
    report('PASS: clearing persistent storage starts empty');
  } finally { if (db) await db.close(); }
}
