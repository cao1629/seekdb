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
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Worker } from 'node:worker_threads';
import { Database, SqlError } from '../../src/wasm/database.mjs';
import { EXAMPLES } from '../../src/wasm/shell-examples.mjs';
import { takeStatement } from '../../src/wasm/shell-sql.mjs';

if (!process.argv[2]) throw new Error('Usage: node test_shell_examples.mjs <seekdb_wasm_database.mjs>');
const moduleURL = pathToFileURL(resolve(process.argv[2]));
const workers = new Set();
const workerFactory = url => {
  const worker = new Worker(url, {
    workerData: { serverURL: new URL('../../src/wasm/worker-server.mjs', import.meta.url).href },
  });
  workers.add(worker);
  worker.on('exit', () => workers.delete(worker));
  return worker;
};
const timer = setTimeout(() => {
  console.error('shell-examples: timed out');
  process.exit(124);
}, 120000);
const decoder = new TextDecoder();
let db;

try {
  db = await Database.open({
    moduleURL,
    workerFactory,
    workerURL: new URL('./database_node_worker.mjs', import.meta.url),
  });
  const session = await db.connect();
  const sqlOptions = {};
  async function query(sql) {
    const rows = [];
    for await (const event of session.query(sql)) {
      if (event.kind === 'row') {
        rows.push(event.values.map(value => value === null ? null : decoder.decode(value)));
      }
      if (event.kind === 'complete' && 'affectedRows' in event) {
        assert.equal(typeof event.affectedRows, 'bigint');
        sqlOptions.noBackslashEscapes = Boolean(event.status & 512);
      }
    }
    return rows;
  }
  async function executeScript(sql) {
    const results = [];
    while (sql) {
      const next = takeStatement(sql, sqlOptions);
      if (!next) break;
      const rows = await query(next.statement);
      if (rows.length) results.push(rows);
      sql = next.rest;
    }
    return results;
  }

  for (let pass = 1; pass <= 2; pass++) {
    assert.deepEqual(await executeScript(EXAMPLES.quickstart), [[
      ['1', 'Browser SQL', 'WebAssembly'],
      ['2', 'Vector search', 'Search'],
      ['3', 'Safe experiments', 'Transactions'],
    ]]);
    assert.deepEqual(await executeScript(EXAMPLES.vector), [
      [['1', 'Reference vector', '0'], ['2', 'One unit away', '1'], ['3', 'Three units away', '3']],
      [['1', 'Reference vector', '0'], ['2', 'One unit away', '1']],
    ]);
    assert.deepEqual(await executeScript(EXAMPLES.transaction), [
      [['Before transaction', '100']],
      [['Inside transaction', '125']],
      [['After rollback', '100']],
    ]);
    console.log(`shell-examples: quickstart, exact search, ANN and rollback passed (run ${pass})`);
  }

  const plan = await query('EXPLAIN SELECT id FROM shell_vectors ORDER BY l2_distance(embedding, [1,0,0]) APPROXIMATE LIMIT 2');
  assert.ok(plan.flat().some(line => line.includes('VECTOR INDEX SCAN') && line.includes('shell_hnsw')));
  assert.deepEqual(await query('SELECT COUNT(*) FROM shell_notes'), [['3']]);
  assert.deepEqual(await query('SELECT COUNT(*) FROM shell_vectors'), [['3']]);
  assert.deepEqual(await query('SELECT id, balance FROM shell_accounts'), [['1', '100']]);
  console.log('shell-examples: HNSW index plan and idempotency passed');

  const modes = (await query('SELECT @@SESSION.sql_mode'))[0][0];
  assert.deepEqual(await executeScript(String.raw`SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES'; SELECT 1; SELECT 'C:\'; SET SESSION sql_mode = ''; SELECT 'a\'b';`),
    [[['1']], [['C:\\']], [["a'b"]]]);
  await query(`SET SESSION sql_mode = '${modes}'`);
  await assert.rejects(query('SELECT * FROM shell_missing_table'), SqlError);
  assert.deepEqual(await query("SELECT NULL, CAST(18446744073709551615 AS UNSIGNED), 'hello;world'"),
    [[null, '18446744073709551615', 'hello;world']]);
  console.log('shell-examples: SQL mode changes, errors and exact values passed');

  const decimalCases = [
    [18, '1234567890123456.78'],
    [18, '-1234567890123456.78'],
    [18, '21474836.47'],
    [18, '21474836.48'],
    [18, '-21474836.49'],
    [18, '42949672.95'],
    [18, '42949672.96'],
    [38, '123456789012345678901234567890123456.78'],
    [38, '-123456789012345678901234567890123456.78'],
    [38, '100000000042949672.96'],
    [65, `${'1234567890'.repeat(6)}123.45`],
    [65, `1${'0'.repeat(62)}.01`],
  ];
  for (const [precision, expected] of decimalCases) {
    const expression = `CAST('${expected}' AS DECIMAL(${precision},2))`;
    assert.deepEqual(await query(`SELECT ${expression}, CAST(${expression} AS CHAR)`),
      [[expected, expected]], `DECIMAL(${precision},2) must preserve ${expected}`);
  }
  assert.deepEqual(await query("SELECT CAST(CAST('1234567890123456.78' AS DECIMAL(18,2)) * 100 AS SIGNED), CAST('-1234567890123456.78' AS DECIMAL(18,2)) + CAST('1234567890123456.00' AS DECIMAL(18,2))"),
    [['123456789012345678', '-0.78']]);
  console.log('shell-examples: decimal precision, wide integer chunks and decimal-to-text casts passed');

  const portabilityFailures = [];
  async function checkPortability(name, check) {
    try {
      await check();
      console.log(`shell-examples: ${name} passed`);
    } catch (error) {
      console.error(`shell-examples: ${name} failed: ${error.message}`);
      portabilityFailures.push(error);
    }
  }

  await checkPortability('indexed table TRUNCATE and DROP', async () => {
    await query('CREATE TABLE shell_ddl (id INT PRIMARY KEY, value INT, INDEX shell_ddl_value_idx(value))');
    await query('INSERT INTO shell_ddl VALUES (1, 10), (2, 20)');
    assert.deepEqual(await query('SELECT COUNT(*) FROM shell_ddl FORCE INDEX(shell_ddl_value_idx) WHERE value >= 0'), [['2']]);
    await query('TRUNCATE TABLE shell_ddl');
    assert.deepEqual(await query('SELECT COUNT(*) FROM shell_ddl'), [['0']]);
    assert.deepEqual(await query('SELECT COUNT(*) FROM shell_ddl FORCE INDEX(shell_ddl_value_idx) WHERE value >= 0'), [['0']]);
    await query('INSERT INTO shell_ddl VALUES (3, 30)');
    assert.deepEqual(await query('SELECT id FROM shell_ddl FORCE INDEX(shell_ddl_value_idx) WHERE value = 30'), [['3']]);
    await query('DROP TABLE shell_ddl');
    assert.deepEqual(await query("SHOW TABLES LIKE 'shell_ddl'"), []);
    await assert.rejects(query('SELECT * FROM shell_ddl'), error => error instanceof SqlError && error.code === 1146);
  });

  await checkPortability('BIN, OCT and CONV integer widths', async () => {
    const values = [
      0n, 1n, 2147483647n, 2147483648n, 4294967295n, 4294967296n,
      17179869184n, 21474836480n, 34359738368n, 42949672960n,
      9223372036854775807n, 18446744073709551615n,
    ];
    for (const value of values) {
      assert.deepEqual(await query(`SELECT BIN(${value}), OCT(${value}), CONV('${value}',10,10), CONV('${value}',10,16)`),
        [[value.toString(2), value.toString(8), value.toString(10), value.toString(16).toUpperCase()]],
        `Base conversion must preserve ${value}`);
    }
    assert.deepEqual(await query("SELECT CONV('FFFFFFFFFFFFFFFF',16,-10), CONV('-1',10,16), CONV('-9223372036854775808',10,-16), CONV('8000000000000000',16,-10), CONV('-8000000000000000',-16,10), BIN(-1), OCT(-1)"),
      [['-1', 'FFFFFFFFFFFFFFFF', '-8000000000000000', '-9223372036854775808', '9223372036854775808', (2n ** 64n - 1n).toString(2), (2n ** 64n - 1n).toString(8)]]);
  });

  await checkPortability('SQL type metadata', async () => {
    await query('CREATE TABLE shell_type_metadata (id BIGINT, amount DECIMAL(18,2), label VARCHAR(20), observed DATETIME(6), duration TIME(3), updated TIMESTAMP(4) NULL DEFAULT NULL)');
    const expectedTypes = [
      ['id', 'bigint(20)'], ['amount', 'decimal(18,2)'], ['label', 'varchar(20)'],
      ['observed', 'datetime(6)'], ['duration', 'time(3)'], ['updated', 'timestamp(4)'],
    ];
    const createTable = (await query('SHOW CREATE TABLE shell_type_metadata'))[0][1];
    for (const [name, type] of expectedTypes) {
      assert.ok(createTable.split('\n').some(line => line.trimStart().startsWith(`\`${name}\` ${type} `)),
        `SHOW CREATE TABLE must include ${name} ${type}: ${createTable}`);
    }
    assert.deepEqual((await query('SHOW COLUMNS FROM shell_type_metadata')).map(row => row.slice(0, 2)), expectedTypes);
    assert.deepEqual(await query("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'playground' AND TABLE_NAME = 'shell_type_metadata' ORDER BY ORDINAL_POSITION"), expectedTypes);
  });

  await checkPortability('DUMP signed and multiword decimal integers', async () => {
    const dumpCases = [
      [18, 8, '1234567890123456.78'],
      [18, 8, '-1234567890123456.78'],
      [38, 16, '123456789012345678901234567890123456.78'],
      [38, 16, '-123456789012345678901234567890123456.78'],
      [65, 32, `${'1234567890'.repeat(6)}123.45`],
      [65, 32, `-${'1234567890'.repeat(6)}123.45`],
    ];
    const wordMask = 2n ** 64n - 1n;
    for (const [precision, bytes, value] of dumpCases) {
      const scaled = BigInt(value.replace('.', ''));
      const bits = BigInt.asUintN(bytes * 8, scaled);
      const items = bytes === 8 ? String(scaled)
        : Array.from({length: bytes / 8}, (_, index) => String((bits >> BigInt(index * 64)) & wordMask)).join(',') + ',';
      const expected = `"precision=${precision} scale=2 int_bytes=${bytes} items=[${items}]"`;
      assert.deepEqual(await query(`SELECT DUMP(CAST('${value}' AS DECIMAL(${precision},2)))`), [[expected]]);
    }
  });

  if (portabilityFailures.length) throw new AggregateError(portabilityFailures, 'Wasm integer-width regressions failed');

  await session.close();
  await db.close();
  assert.equal(workers.size, 0);
  console.log('shell-examples: all assertions passed');
} finally {
  if (db) await db.close().catch(() => {});
  await Promise.all([...workers].map(worker => worker.terminate()));
  clearTimeout(timer);
}
