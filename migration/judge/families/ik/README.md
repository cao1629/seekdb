# Family 13: derived data and the IK tokenizer

PLAN.md section 4, family 13. The family has two parts.

- **The geometry, vector_index and fts_index suites, compared through their query results.** PLAN.md
  counts 42, 22 and 15 tracked files; 39, 21 and 15 of them are configured cases
  (../../census/portable.txt), so they are among the 272 cases that family 1 records and compares.
  This family runs nothing more for them.
- **A differential corpus for the IK tokenizer.** `generate.py` writes a mysqltest corpus into
  `cases/` that sends Chinese, mixed and edge-case text through the IK tokenizer in both of its modes
  and prints the tokens, and runs the same kind of text through fulltext indexes built with the IK
  parser. The judge records the corpus on the C++ reference and on the Rust build with the runner
  (`--test-dir`, reduced init) and compares the recordings byte for byte (../../harness/README.md,
  "compare").

The generator reads nothing but itself and never talks to a server. The corpus was first run on
2026-09-28; what the runs found is in "Live check, 2026-09-28".

## Commands

```
python3 migration/judge/families/ik/generate.py cases
python3 migration/judge/families/ik/generate.py cases --check
python3 migration/judge/families/ik/generate.py check-recording --record-dir <record-dir>
python3 migration/judge/families/ik/generate.py diff-tokens --left <record-dir> --right <record-dir>
```

- `cases` writes `cases/*.test` from the text lists in the script and removes `.test` files it did
  not write. The output depends on nothing else, so two runs give the same bytes.
- `cases --check` writes nothing and exits 1 if `cases/` differs from a fresh generation.
- `check-recording` reads one recording (the runner's `--record-dir`) and, for every statement, finds
  its echo and the `errno` line after it. It exits 1 when a file has no recording, a statement or its
  `errno` line is not found, a statement got a client error (2000-2999: the connection was lost), or
  a statement's errno is not the one the reference gives (the generator holds the expected errno of
  every statement: 0, or the error the reference returns). For a statement that prints tokens it also
  checks the shape: the JSON parses, no token appears twice, `doc_len` is the sum of the counts,
  multi-row statements print the expected number of rows, and each long input has the expected
  `CHAR_LENGTH`. It compares nothing with another recording.
- `diff-tokens` is a reading aid for a comparison that failed: for each statement that two
  recordings print differently it says `same tokens, other order` (the same words with the same
  counts, listed in another order), `tokens differ` (with the words found on one side only and the
  counts that changed), or that the errno or other output differs. The verdict stays `compare`'s,
  which is byte for byte; `diff-tokens` does not change it. It exits 1 when any statement differs.

## How SQL reaches the IK tokenizer

Source lines at 834bbee1e. The parser is chosen by name: `ObFTParser::get_desc` returns
`ObIKFTParserDesc` for `ik` (src/storage/fts/ob_fts_parser_helper.cpp:115-140, :130), and
`ObIKFTParserDesc::segment` creates the tokenizer (src/storage/fts/ob_ik_ft_parser.cpp:193-213). Every
SQL path below goes through `ObFTParseHelper::init` and `ObFTParseHelper::segment`
(ob_fts_parser_helper.cpp:327-351 and :361-398, which calls the loop at :258-310).

### The TOKENIZE function

`TOKENIZE(text [, parser [, properties]])` (`ObExprTokenize`, registered at
src/sql/engine/expr/ob_expr_operator_factory.cpp:1017) returns the tokens as JSON:

- The text is the first argument's string, with the argument's collation
  (src/sql/engine/expr/ob_expr_tokenize.cpp:296-314). A NULL value becomes the empty string. The
  parser name is the second argument, trimmed; NULL or absent means `space` (:316-340;
  `OB_DEFAULT_FULLTEXT_PARSER_NAME`, src/oblib/lib/ob_define.h:1444). The third argument is a JSON
  array of objects (:342-373): `{"output": "default" | "all"}` and
  `{"additional_args": [{"ik_mode": "smart" | "max_word"}]}`. Only the first key of each object is
  read (:147), later entries override earlier ones, and `ik_mode` defaults to `smart`
  (src/storage/fts/ob_fts_parser_property.cpp:553-634).
- For `ik`, `try_load_dictionary_for_ik` (ob_expr_tokenize.cpp:392-410) asks for a dictionary loader
  of the text's character set, and only utf8mb4 has one
  (src/storage/fts/dict/ob_gen_dic_loader.cpp:147-184); a binary string, a number and a NULL literal
  (whose collation is binary) fail there with 1235 "ik with the binary charset is not supported". The
  loader itself writes nothing: `check_need_load_dic` always answers no
  (src/storage/fts/dict/ob_dic_loader.cpp:144-150).
- `tokenize_fulltext` (ob_expr_tokenize.cpp:78-124) segments the text into an `ObFTWordMap` with
  `MIN(MAX(len / 2, 2), 997)` buckets for a text of `len` bytes, then prints it:
  `make_token_array_json` for `default`, a JSON array of the words; `make_detail_json` for `all`, an
  object with `tokens` (one `{"word": count}` per word) and `doc_len`
  (ob_fts_parser_helper.cpp:421-506). Both walk the hash map, so the words come in the map's order,
  which depends on the word hash (`ObFTWord::hash`, the collation's datum hash with seed 0,
  src/storage/fts/ob_fts_struct.cpp:28-40) and on the bucket count. The order is part of TOKENIZE's
  output and is compared like the rest.

### A fulltext index WITH PARSER ik

- **DDL.** `WITH PARSER ik PARSER_PROPERTIES=(ik_mode=...)` is checked by
  `ik_rebuild_props_for_ddl` (ob_fts_parser_property.cpp:553-634): a name is accepted when it matches
  `ik_mode` or one of the three dictionary table names without regard to case (:489-513), `ik_mode`
  must be `smart` or `max_word` in any case, and a missing `ik_mode` becomes `smart`.
- **DML.** Inserting, updating or deleting a row segments the indexed text:
  `ObFTDMLIterator::generate_ft_word_rows` (src/sql/das/ob_das_domain_utils.cpp:1019-1039) calls
  `ObDASDomainUtils::generate_fulltext_word_rows` (:276-337), which segments the text with the index's
  parser name and properties (`segment_and_calc_word_count`, :339-357) and writes one row per word
  with its count and the document length. With `enable_strict_defensive_check` the same text is
  segmented again to check the rows (:1098-1112). The DML path reads a TEXT column's content, not its
  LOB locator.
- **Building an index on existing rows.** `CREATE FULLTEXT INDEX` on a table that holds rows scans it
  with an `ObFTIndexRowCache` (src/sql/engine/table/ob_table_scan_op.cpp:1440 and :3676), whose
  `segment` calls the same `generate_fulltext_word_rows` (ob_das_domain_utils.cpp:82-100).
- **Searching.** In natural-language mode the search text is segmented with the index's parser, both
  when the plan is built (`ObJoinOrder::get_query_tokens`, src/sql/optimizer/ob_join_order.cpp:16685-16745)
  and when it runs (`ObDASTextRetrievalMergeIter::build_query_tokens`,
  src/sql/das/iter/ob_das_text_retrieval_merge_iter.cpp:160-297, the parser at :243-245). In boolean
  mode the search text is lowercased and parsed by the boolean-syntax parser `fts_parse_docment`
  (:175-219); the IK tokenizer is not called, so a boolean term matches only a word that the index
  holds as it is.

### What the tokenizer does with the text

- **Dictionaries.** The main dictionary (275,908 words), the quantifier dictionary (316 units such as
  个, 公斤 and 平方公里) and a stopword list are compiled into the binary
  (src/storage/fts/dict/ob_ik_dic.cpp:29, :275948, :276011). The first use builds double-array tries
  from them in parallel threads and keeps them in the `dict_cache` KV cache
  (src/storage/fts/dict/ob_ft_dict_hub.cpp:49-84, src/storage/fts/dict/ob_ft_range_dict.cpp:48-229);
  on the reference this takes well under a second. The stopword check is commented out
  (ob_ik_ft_parser.cpp:93-97), so no word is dropped as a stopword.
- **Characters.** Each character is classified (src/storage/fts/ik/ob_ik_char_util.h:444-500,
  src/storage/fts/utils/unicode_utils.cpp:28-127): ASCII and fullwidth letters, ASCII and fullwidth
  digits, Chinese (U+4E00-9FFF, U+F900-FAFF, U+3400-4DBF), other CJK (U+FF00-FFEF, Hangul, Hiragana,
  Katakana), and everything else, which counts as a separator. So accented Latin letters, Greek,
  Cyrillic, emoji, CJK punctuation (U+3000-303F, which holds 。, 〇 and the ideographic space) and
  characters outside the BMP separate words, while fullwidth punctuation such as ， falls in
  U+FF00-FFEF and counts as other CJK: it never ends a batch (below) and is only dropped when it would
  come out as a word of its own.
- **Processors.** Four processors see every character in this order (ob_ik_ft_parser.cpp:310-338,
  :131-145): letters (`ObIKLetterProcessor`: English runs, digit runs joined by `,` and `.`, and mixed
  runs joined by `# & + - . @ _`, src/storage/fts/ik/ob_ik_letter_processor.cpp), quantifiers
  (Chinese numerals and the quantifier dictionary after a number,
  src/storage/fts/ik/ob_ik_quantifier_processor.cpp), CJK words (main dictionary matches,
  src/storage/fts/ik/ob_ik_cjk_processor.cpp) and surrogates (UTF-16 only; never for utf8mb4).
- **Choosing tokens.** `ObIKArbitrator` groups overlapping candidates. In `max_word` mode every
  candidate is kept; in `smart` mode one non-overlapping set is chosen per group by
  `ObIKTokenChain::better_than` (src/storage/fts/ik/ob_ik_token.cpp:113-151: more text covered, fewer
  words, longer span, later end, then the product and the position weights of the word lengths).
  `output_result` then walks the text and adds every Chinese or other-CJK character that no chosen
  word covers as a one-character word, except fullwidth ASCII punctuation
  (src/storage/fts/ik/ob_ik_arbitrator.cpp:110-215). In smart mode a number is joined to the Chinese
  numeral or quantifier right after it (`TokenizeContext::compound`,
  src/storage/fts/ik/ob_ik_processor.cpp:206-259: `3个`, `1.2亿`).
- **Batches.** The text is processed in batches: a batch ends at the first separator that comes after
  the batch's first 1,001 characters (`SEGMENT_LIMIT` is 1,000, src/storage/fts/ob_ik_ft_parser.h:92;
  the test at ob_ik_ft_parser.cpp:167). The 272 configured cases never end a batch there (line 168 ran
  0 times in the coverage profile). What this does to long texts is under "What the reference does
  that a translation must keep".
- **Words.** `ObAddWord` lowercases each word with the text's collation and counts repeats
  (src/storage/fts/ob_fts_stop_word.cpp:131-218); the IK parser asks for no stopword filter and no
  length limits (ob_ik_ft_parser.cpp:223-229). `doc_len` is the number of words including repeats.

## What the corpus holds

22 files, 1,253 statements (counts from `cases`). Every statement is followed by
`--echo errno $mysql_errno`.

| Files | What | Statements |
|---|---|---|
| s1_cjk (6) | 77 sentences of everyday, news and technical Chinese; 30 sentences whose segmentation is ambiguous (南京市长江大桥, 结婚的和尚未结婚的, 研究生命起源, ...), also in the default form; 16 lines of classical Chinese and idioms; 12 lists of place, organization and personal names; 6 sentences in traditional characters | 318 |
| s2_mixed (2) | 52 sentences mixing Chinese with English, versions, numbers, units, URLs, email addresses and code | 106 |
| s3_numbers (2) | 48 texts of Chinese numerals (including 壹贰叁, 廿卅, 两, 〇), quantifiers (个, 公斤, 平方公里, 点钟), Arabic numbers with `,` and `.` in every position, fullwidth digits, percentages, dates and currency | 98 |
| s4_letters (2) | 40 texts of English: case, every letter connector before, between and after letters and digits, emails, URLs, paths, versions, identifiers, fullwidth letters | 82 |
| s5_chars (2) | 40 texts for the character classes: ASCII, Chinese and fullwidth punctuation, whitespace and control characters, NBSP, zero-width space, BOM, Japanese, Korean (syllables and jamo), halfwidth katakana, extension A and compatibility ideographs, characters outside the BMP, emoji, other scripts, enclosed numbers | 82 |
| s6_edge (4) | 27 short and degenerate texts (empty, blanks, one character of each class, repeats, case), also in the default form; errors and odd arguments (NULL, binary, numbers, invalid UTF-8, parser names, every property form the source reads, property names in other cases and spellings); JSON functions over the result; 6 texts with `COLLATE utf8mb4_bin`; VARCHAR columns (with NULL values) and a TEXT column read from tables; TOKENIZE in a generated column | 186 |
| s7_long (1) | 20 long texts built with REPEAT and CONCAT in a VARCHAR(40000) column: 1,000 characters without a separator, a separator after the first 1,000, 1,001 and 1,002 characters (the file's titles count from 0), batches ended by 。 and not by ，, English, mixed text, one token of 5,000 letters and one of 3,000 digits, a 12,320-character paragraph, Hangul, emoji only, underscore identifiers | 63 |
| s8_index (3) | a table with a smart and a max_word IK index over 30 documents; 53 boolean terms and 14 natural-language searches on both indexes, boolean operators, UPDATE, DELETE and INSERT, and searches again; an index built on existing rows; an IK index on a TEXT column; index DDL the parser refuses; the default mode; long documents in an index | 318 |

Each text is tokenized twice, `smart` and `max_word`, with `"output": "all"`, so the counts and
`doc_len` are printed too:

```
SELECT tokenize('<text>', 'ik', '[{"output": "all"}, {"additional_args": [{"ik_mode": "smart"}]}]') AS smart;
SELECT tokenize('<text>', 'ik', '[{"output": "all"}, {"additional_args": [{"ik_mode": "max_word"}]}]') AS max_word;
```

The fulltext statements print the matching ids, ordered by id, and each row's relevance, `MATCH ...
AGAINST ...` in the select list as `score` (since 2026-09-28). File names have no dot before
`.test`, as `--test-dir` requires, and sort in the order of the table.

## Determinism

- Each file starts with `SET NAMES utf8mb4 COLLATE utf8mb4_general_ci, ob_enable_plan_cache = 0,
  ob_query_timeout = 600000000, ob_trx_timeout = 600000000`.
- The plan cache is off because it changed an argument's type while the corpus was probed: with it
  on, `tokenize(repeat('ab', 3), 'ik', '[{"output": "all"}]')` failed with 1210 after
  `tokenize(repeat('abcd', 300), 'ik', '[{"output": "all"}]')` had been planned. REPEAT's result type
  depends on the value of its count (VARCHAR up to 512 characters, LONGTEXT above;
  src/sql/engine/expr/ob_expr_repeat.cpp:60-80), the second statement reused the first one's plan with
  a LONGTEXT argument, and TOKENIZE fails on LONGTEXT (item 2 below). With the plan cache off each
  statement gets its own plan. Plan cache hits are family 7's.
- In every TOKENIZE expected to succeed, the text is a literal, a VARCHAR column or one `CAST` of a
  TEXT column; the other forms (NULL, binary strings, numbers, TEXT values) are recorded with the
  errors they give.
- Every statement that can return more than one row has `ORDER BY id`.
- The fulltext statements print the ids and the relevance. BM25 uses the optimizer's estimate of the
  table's row count when there is one (`estimated_total_doc_cnt_`,
  src/sql/code_generator/ob_tsc_cg_service.cpp:1897-1908; ob_das_text_retrieval_merge_iter.cpp:682-733),
  and that estimate could move with statistics, so the relevance was first left out on that reading
  alone. A review of the second set asked for the two recordings instead: with the relevance in the
  select list, two recordings of the reference agree on every value (225 score rows; "Live check,
  2026-09-28", below), so it is compared like the rest. Each file creates its tables and the corpus
  gathers no statistics. Whether a document matches does not depend on the estimate: every word's
  weight is at least a small positive number (`ObExprBM25::query_token_weight`,
  src/sql/engine/expr/ob_expr_bm25.cpp:152-161).
- Table names start with `ik_s6_`, `ik_s7_` or `ik_s8_` and differ between files, and each file drops
  the tables it created, so the files do not depend on each other or on their order.
- The token order in TOKENIZE's output is the hash map's order. It is fixed for a build (the hash has
  a fixed seed and the map a fixed bucket count per text length) and six recordings agree on it; a
  build with another hash or map prints the same words in another order, which `compare` counts as a
  difference and `diff-tokens` labels `same tokens, other order`.

## Running the corpus

Under the reduced init, retries off and the trailing-whitespace tolerance off, with the check that
tools/deploy and sdb.py are 834bbee1e's passing first:

```
python3 .github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
  --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --mysqltest /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest \
  --base-dir <base-dir> --work-dir <work-dir> --port <port> \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql \
  --test-dir migration/judge/families/ik/cases --record-dir <record-dir>
```

Then `compare --left <record-1> --right <record-2>`, and `generate.py check-recording --record-dir
<record-1>` on the C++ recording. A run takes about 5 seconds, most of it starting the instance.

## Live check, 2026-09-28

Every run below used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb,
sha256 db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91) with the archive's obclient
and mysqltest, port 3892, the reduced init, `--max-retries 0 --no-ignore-trailing-whitespace`,
`--test-dir cases` and `--record-dir`; the check that tools/deploy and sdb.py are 834bbee1e's passed
before each. The outputs are in /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/08-ik/: one
directory per run (`rNN`, each with check-recording.txt), the command in run.sh, the comparisons in
compare-*.log and .json. The runner was the live file (sha256
29e19d689317750c6d55e2b61e8c100ce93a4cfc5ddc86c6ec76a67d5ae188b6); this unit did not change it.
Before and between the runs, single statements and trial passes of the corpus ran against a separate
instance on the same port (scratch.sh, mt.sh), never at the same time as a run; analysis/ holds them:
probes/ (the statements each finding below comes from), trial1 to trial3, crash-tokenize-null/, and
underscore-statements.txt (the statements the mutation changes).

### Result

- **Three recordings compare identical.** r04, r05 and r06 record the corpus as it now stands
  (`test_dir_sha256` 47ca1ec946819c93ab1593cc09485083097452b6bf47a766e8dddf080438f753): `compare`
  exits 0 for each of the three pairs, 22 of 22 identical, no recording problems.
- **`check-recording` exits 0 on each:** 1,253 statements; 42 fail with the reference's error as
  expected; 843 token lists are well formed (`doc_len` equal to the sum of the counts, no token twice,
  the expected rows and lengths).
- r01, r02 and r03 recorded the corpus before the six statements on property names were added
  (`test_dir_sha256` 2149144e0d56b2f0cae6e4dc0f5188f03ccfa28d91c60e3385636977d73cf4aa, 1,247
  statements): identical in all three pairs as well, and byte-identical to r04 in the 20 files the
  addition did not touch.
- Nothing had to be left out as not comparable.

### The relevance column (2026-09-28)

A review of the second set pointed out that relevance through the IK index, which BM25 computes from
the word counts and `doc_len` the index path writes, was never compared, on a reason read from the
source only. `ids_query` in generate.py now puts `MATCH(<column>) AGAINST(<query>[ IN BOOLEAN MODE])
AS score` in the select list of every s8 search (214 statements, and the error case `a.b@c.com`), so
a defect in the index's word counts or `doc_len` that keeps the same rows matching changes the scores.
The corpus is still 22 files and 1,253 statements; `test_dir_sha256` is now
`4cb3eebf5fc684db043c843f9b181f1ebb8ef829f6e6566a2ddc7c7c0009124c`. Two recordings on the reference, ik-r07 and ik-r08
(/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/, port 3892, same options as above,
2026-09-28 21:30), compare identical, 22 of 22, and `check-recording` finds 0 problems in ik-r07. They
print 225 score rows, from 0.059 to 36.9 in this corpus, the same in both, and the ids of every search are
the ids r04 printed. So the column is compared.

### What the probes found and how the corpus handles it

1. **A NULL third argument crashes the server.** `SELECT tokenize('中华人民共和国', 'ik', NULL)` ended
   the scratch instance (2013, then 2006 for every later statement; analysis/crash-tokenize-null/).
   `parse_parser_properties` calls `ObJsonExprHelper::get_json_doc`, which for a NULL argument sets
   `is_null` and leaves `base` null, and then reads `base->json_type()` without looking at `is_null`
   (ob_expr_tokenize.cpp:353-356; src/sql/engine/expr/ob_expr_json_func_helper.cpp:121-123). Any parser
   name reaches the same line. The corpus leaves it out ("Excluded from the corpus").
2. **TOKENIZE reads a LOB's locator, not its text.** `parse_fulltext` takes `get_string()` of the
   argument's datum (ob_expr_tokenize.cpp:308) and never reads the LOB through
   `ObTextStringHelper::read_real_string_data`, as other string functions do (REPEAT, for one,
   src/sql/engine/expr/ob_expr_repeat.cpp:261). So a TEXT value, or a REPEAT result longer than 512
   characters, reaches the IK tokenizer as its LOB locator, and the tokenizer fails with 1210 "Invalid
   argument". `CAST(c AS CHAR(100))` gives it the text. The fulltext index reads the same TEXT column
   correctly. The corpus records the two TEXT errors, the cast and an IK index on a TEXT column
   (s6_edge_0004, s8_index_0002).
3. **The plan cache changed an argument's type** ("Determinism"); the corpus turns it off.
4. **A NULL literal and a NULL column value differ.** `tokenize(NULL, 'ik')` fails with 1235 (a NULL
   literal has the binary collation), while a NULL read from a VARCHAR column prints `[]` (the
   column's collation). The generator first expected 1235 for the column; the first trial printed
   `[]`, and the expectation was corrected. Both are in s6_edge_0004.
5. **Property names.** Names are checked without regard to case (ob_fts_parser_property.cpp:489-513)
   but looked up exactly, so `{"IK_MODE": "max_word"}` and even `{"Ik_Mode": "bad"}` are accepted and
   leave the mode at smart. The quantifier table's name is spelled `quanitfier_table` in the source
   (src/data_plane/api/data_plane/fts/ob_fts_literal.h:39): `quanitfier_table` is accepted,
   `quantifier_table` is refused with 1235 "ik config not supported". `dict_table` and
   `stopword_table` are accepted and not used. The six statements that show this were added after
   r01-r03 (s6_edge_0003).
6. **TOKENIZE in a generated column fails** at CREATE TABLE with 4016 "Internal error", with the IK
   parser and with the default one, although the function is marked valid for generated columns
   (ob_expr_tokenize.cpp:47). The corpus records one such CREATE TABLE.
7. `a.b@c.com` as a boolean-mode search fails with 1149 (the boolean parser's syntax); the corpus
   records it.

### What the reference does that a translation must keep

The corpus records these as they are; a Rust build that changes any of them shows up as a difference.

- **Long texts get extra one-character words.** Each batch's `output_result` walks the whole text from
  its first byte (ob_ik_arbitrator.cpp:116), but only the batch's own word groups are in its `chains_`
  map, so every Chinese and other-CJK character outside the batch comes out once more as a word of its
  own.
  From r04, s7_long_0001: `中国人民 ` 250 times (1,250 characters, two batches) prints `中国人民` 250
  times and each of 中, 国, 人, 民 250 times; 3,300 characters of `人工智能技术发展迅速。` (four
  batches) print each character 900 times; the same text joined by the fullwidth comma `，`, which
  does not end a batch, has no one-character words; `한국어 ` 300 times prints each syllable 600 times.
  A long document in a fulltext index holds these words too: in s8_index_0003 the boolean search `民`
  finds the document `中国人民 ` × 250.
- **A batch can end inside a mixed token.** The separator that ends a batch can be a connector such as
  `_` or `-` in the middle of a mixed letter-and-digit run. The batch then outputs the English words
  before it as words of their own, and the letter processor, which keeps its state from one batch to
  the next, completes the mixed token in the next batch. `snake_case_name ` 200 times prints, in smart
  mode, `snake_case_name` 200 times plus `snake` twice and `case` once (s7_long_0001, row 20);
  analysis/probes/ik_t11 shows the words appear from 63 repeats (1,008 characters) on and not at 62.
- **The token order is the hash map's order** ("Determinism").
- **Only the first key of each properties object counts:** `[{"output": "all", "additional_args":
  [{"ik_mode": "max_word"}]}]` prints the `all` form in smart mode, because `additional_args` is never
  read; property names behave as in item 5.
- **Connectors stay in a mixed token at its end:** `a_` and `init__` are tokens, and so is `a._b`.
- **Fullwidth ASCII punctuation (U+FF01-FF5E) is dropped, but the other forms in U+FF00-FFEF are
  words:** `￥`, `￡`, `｟｠`, the halfwidth `｡｢｣､` and halfwidth katakana come out as one-character
  words (s5_chars).
- `TokenizeContext::compound` reads the first element of `result_list_` after it may have emptied the
  list (ob_ik_processor.cpp:241). `ObList` keeps no element in its list head (the head holds only the
  two links, src/oblib/lib/list/ob_list.h:246-258 and :564, and `get_first` returns `root_.next->data`,
  :423), so for an empty list this reads memory past the head. The reference appends nothing there in
  every recorded case (`3个` at the end of a text prints `3个` in smart mode); a translation must also
  append nothing when the list is empty.

### Not comparable

None so far: the six recordings agree on every statement of their corpus.

### Excluded from the corpus

| What | Why | Where in the source |
|---|---|---|
| `TOKENIZE(x, parser, NULL)` | crashes the reference (item 1) | ob_expr_tokenize.cpp:353-356 |
| A computed first argument while the plan cache is on | a reused plan can change the argument's type to LONGTEXT (item 3) | ob_expr_repeat.cpp:60-80 |

## The mutation for the second sign-off

migration/judge/mutations/second-set/08-ik-underscore-not-letter-connector.patch, with its note beside
it: the loop in `ObUnicodeBlockUtils::check_letter_connector` stops one entry early
(src/storage/fts/utils/unicode_utils.cpp:67), so `_` no longer joins letters and digits into one
token. In the 272 configured cases only fts_index.cn_word sends text through the IK parser, and none of
its text has an underscore after a letter or digit; in the corpus, 38 token lists hold a token with an
underscore, and 14 boolean searches and one `MEMBER OF` test find a row through one, in 11 of the 22
files. Caught on 2026-09-28 and again on 2026-09-29 with the relevance column: against ik-r07 the
same 11 files differ, and besides the 16 searches whose ids change, 36 more differ only in their
relevance (the note beside the patch).
