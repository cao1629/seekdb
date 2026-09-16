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

export const EXAMPLES = Object.freeze({
  quickstart: `CREATE DATABASE IF NOT EXISTS playground;
USE playground;
CREATE TABLE IF NOT EXISTS shell_notes (
  id INT PRIMARY KEY,
  title VARCHAR(100),
  topic VARCHAR(40)
);
INSERT IGNORE INTO shell_notes VALUES
  (1, 'Browser SQL', 'WebAssembly'),
  (2, 'Vector search', 'Search'),
  (3, 'Safe experiments', 'Transactions');
SELECT id, title, topic FROM shell_notes ORDER BY id;`,

  vector: `CREATE DATABASE IF NOT EXISTS playground;
USE playground;
CREATE TABLE IF NOT EXISTS shell_vectors (
  id INT PRIMARY KEY,
  label VARCHAR(80),
  embedding VECTOR(3),
  VECTOR INDEX shell_hnsw(embedding)
    WITH (distance=l2, type=hnsw, lib=vsag)
);
INSERT IGNORE INTO shell_vectors VALUES
  (1, 'Reference vector', '[1,0,0]'),
  (2, 'One unit away', '[1,1,0]'),
  (3, 'Three units away', '[1,0,3]');
SELECT id, label, l2_distance(embedding, [1,0,0]) AS distance
FROM shell_vectors
ORDER BY l2_distance(embedding, [1,0,0]) LIMIT 3;
SELECT id, label, l2_distance(embedding, [1,0,0]) AS distance
FROM shell_vectors
ORDER BY l2_distance(embedding, [1,0,0]) APPROXIMATE LIMIT 2;`,

  transaction: `CREATE DATABASE IF NOT EXISTS playground;
USE playground;
CREATE TABLE IF NOT EXISTS shell_accounts (
  id INT PRIMARY KEY,
  balance INT
);
INSERT IGNORE INTO shell_accounts VALUES (1, 100);
SELECT 'Before transaction' AS stage, balance FROM shell_accounts WHERE id = 1;
BEGIN;
UPDATE shell_accounts SET balance = balance + 25 WHERE id = 1;
SELECT 'Inside transaction' AS stage, balance FROM shell_accounts WHERE id = 1;
ROLLBACK;
SELECT 'After rollback' AS stage, balance FROM shell_accounts WHERE id = 1;`,
});
