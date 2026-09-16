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

function scanStatements(sql, { noBackslashEscapes = false, ansiQuotes = false } = {}, firstOnly = false) {
  if (typeof sql !== 'string') throw new TypeError('SQL must be a string.');
  const statements = [];
  let start = 0;
  let hasContent = false;
  let index = 0;

  while (index < sql.length) {
    const character = sql[index];
    if (/\s/u.test(character)) {
      index++;
    } else if (character === ';') {
      if (hasContent) {
        statements.push(sql.slice(start, index).trim());
        if (firstOnly) return { statements, complete: true, rest: sql.slice(index + 1) };
      }
      hasContent = false;
      start = ++index;
    } else if (character === '#' || (sql.startsWith('--', index)
      && (index + 2 === sql.length || /[\s\u0000-\u001f\u007f]/u.test(sql[index + 2])))) {
      while (index < sql.length && sql[index] !== '\n' && sql[index] !== '\r') index++;
    } else if (sql.startsWith('/*', index)) {
      const end = sql.indexOf('*/', index + 2);
      if (end < 0) throw new SyntaxError(`Unterminated block comment at character ${index + 1}.`);
      if (sql[index + 2] === '!'
        && sql.slice(index + 3, end).replace(/^\d{5,6}(?=\s|$)/u, '').trim()) {
        hasContent = true;
      }
      index = end + 2;
    } else if (character === "'" || character === '"' || character === '`') {
      hasContent = true;
      const quoteStart = index++;
      const identifier = character === '`' || (character === '"' && ansiQuotes);
      let closed = false;
      while (index < sql.length) {
        if (sql[index] === '\\' && !identifier && !noBackslashEscapes) {
          index += 2;
        } else if (sql[index] === character) {
          if (sql[index + 1] === character) {
            index += 2;
          } else {
            index++;
            closed = true;
            break;
          }
        } else {
          index++;
        }
      }
      if (!closed) {
        const kind = identifier ? 'quoted identifier' : 'quoted string';
        throw new SyntaxError(`Unterminated ${kind} at character ${quoteStart + 1}.`);
      }
    } else {
      hasContent = true;
      index++;
    }
  }

  const complete = statements.length > 0 && !hasContent;
  if (hasContent) statements.push(sql.slice(start).trim());
  return { statements, complete };
}

export function splitStatements(sql, options) {
  return scanStatements(sql, options).statements;
}

export function takeStatement(sql, options) {
  const result = scanStatements(sql, options, true);
  if (!result.statements.length) return undefined;
  return { statement: result.statements[0], rest: result.rest ?? '' };
}

export function isComplete(sql, options) {
  try {
    return scanStatements(sql, options).complete;
  } catch (error) {
    if (error instanceof SyntaxError) return false;
    throw error;
  }
}

function statementTokens(sql, { noBackslashEscapes = false, ansiQuotes = false } = {}) {
  const tokens = [];
  let index = 0;
  const lineComment = offset => sql[offset] === '#' || (sql.startsWith('--', offset)
    && (offset + 2 === sql.length || /[\s\u0000-\u001f\u007f]/u.test(sql[offset + 2])));
  while (index < sql.length) {
    const character = sql[index];
    if (/\s/u.test(character) || character === ';') {
      index++;
    } else if (lineComment(index)) {
      while (index < sql.length && sql[index] !== '\n' && sql[index] !== '\r') index++;
    } else if (sql.startsWith('/*', index)) {
      const end = sql.indexOf('*/', index + 2);
      if (end < 0) return [];
      if (sql[index + 2] === '!') {
        const content = sql.slice(index + 3, end).replace(/^\d{5}/u, '');
        tokens.push(...statementTokens(content, { noBackslashEscapes, ansiQuotes }));
      }
      index = end + 2;
    } else if (character === '`' || character === '"' || character === "'") {
      const identifier = character === '`' || (character === '"' && ansiQuotes);
      let value = '';
      let closed = false;
      index++;
      while (index < sql.length) {
        if (sql[index] === '\\' && !identifier && !noBackslashEscapes) {
          const escapes = { '0': '\0', b: '\b', n: '\n', r: '\r', t: '\t', Z: '\x1a' };
          const next = sql[++index];
          value += escapes[next] ?? next ?? '';
          index++;
        } else if (sql[index] === character) {
          index++;
          if (sql[index] === character) {
            value += character;
            index++;
          } else {
            closed = true;
            break;
          }
        } else {
          value += sql[index++];
        }
      }
      if (!closed) return [];
      tokens.push(value);
    } else {
      const start = index++;
      while (index < sql.length && !/[\s;'"`]/u.test(sql[index])
        && !lineComment(index) && !sql.startsWith('/*', index)) index++;
      tokens.push(sql.slice(start, index));
    }
  }
  return tokens;
}

export function databaseAfterStatement(sql, currentDatabase, options) {
  const tokens = statementTokens(sql, options);
  const keywords = tokens.map(token => token.toUpperCase());
  if (tokens.length === 2 && keywords[0] === 'USE') return tokens[1];
  if (keywords[0] === 'DROP' && ['DATABASE', 'SCHEMA'].includes(keywords[1])) {
    const name = tokens.length === 3 ? tokens[2]
      : tokens.length === 5 && keywords[2] === 'IF' && keywords[3] === 'EXISTS' ? tokens[4] : undefined;
    if (name === currentDatabase) return null;
  }
  return currentDatabase;
}

export class CommandHistory {
  #entries = [];
  #position = 0;
  #draft = '';
  #limit;

  constructor(limit = 100) {
    if (!Number.isInteger(limit) || limit < 1) {
      throw new RangeError('History limit must be a positive integer.');
    }
    this.#limit = limit;
  }

  push(sql) {
    const entry = sql.trim();
    if (entry && entry !== this.#entries.at(-1)) {
      this.#entries.push(entry);
      if (this.#entries.length > this.#limit) this.#entries.shift();
    }
    this.resetNavigation();
  }

  resetNavigation() {
    this.#position = this.#entries.length;
    this.#draft = '';
  }

  previous(draft = '') {
    if (!this.#entries.length) return undefined;
    if (this.#position === this.#entries.length) this.#draft = draft;
    this.#position = Math.max(0, this.#position - 1);
    return this.#entries[this.#position];
  }

  next() {
    if (this.#position === this.#entries.length) return undefined;
    this.#position++;
    return this.#position === this.#entries.length ? this.#draft : this.#entries[this.#position];
  }
}
