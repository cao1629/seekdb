# 08-ik: the IK tokenizer no longer joins words across an underscore

Family 13, the IK tokenizer corpus (PLAN.md section 4, family 13; families/ik/README.md). Patch:
08-ik-underscore-not-letter-connector.patch, one .cpp file, one line changed.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-28). Not built.

## What it changes

src/storage/fts/utils/unicode_utils.cpp, `ObUnicodeBlockUtils::check_letter_connector`, line 67 at
834bbee1e. The IK letter processor asks this function whether a character that follows a run of
letters and digits joins that run to the next one; the characters that do are listed at line 61:

```
static constexpr char LETTER_CONNECTOR[] = {'#', '&', '+', '-', '.', '@', '_'};
...
for (int i = 0; i < ARRAYSIZEOF(LETTER_CONNECTOR); i++) {
```

The patch writes `ARRAYSIZEOF(LETTER_CONNECTOR) - 1`: the loop stops one entry early and never
compares with the last connector, `_`. An underscore then ends the letter-and-digit token instead of
joining it to what follows, so no IK token can hold an underscore any more: `snake_case` becomes
`snake` and `case`, `admin_01` becomes `admin` and `01`, `a_` becomes `a`. This is the kind of slip a
translation of a bounded loop over a fixed table makes. The other six connectors, the number
connectors (`check_num_connector`, line 75) and every other character class are unchanged.

## Why family 13 catches it

- The caller is `ObIKLetterProcessor::process_mix_letter`
  (src/storage/fts/ik/ob_ik_letter_processor.cpp:151-204): while a run of letters or digits is open,
  every other character goes to `ObFTCharUtil::check_letter_connector` (:171), and a character that
  is a connector and of type USELESS extends the run (:175-177). `_` is USELESS for the IK character
  classes (it is not a letter, digit or CJK character; src/storage/fts/utils/unicode_utils.cpp:28-127),
  so in the reference an underscore inside or at the end of a run is kept in the mix token. Nothing
  else in the source calls `check_letter_connector` (`git grep` at 834bbee1e: only
  src/storage/fts/ik/ob_ik_char_util.h:337 and :401-421, which the letter processor calls).
- Every corpus statement that sends such a text through the IK parser runs the patched line: the
  TOKENIZE statements go through `ObIKFTParserDesc::segment` (src/storage/fts/ob_ik_ft_parser.cpp:193)
  and the letter processor is the first of the four processors for every character
  (ob_ik_ft_parser.cpp:310-338, :131-145); the index statements reach the same parser through the
  DML and index-build paths (families/ik/README.md, "How SQL reaches the IK tokenizer").
- The output that changes, in the C++ recording
  /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/08-ik/r04/rec/ (identical to r05 and r06):
  no mutated build can print a token that holds an underscore, and 38 token lists in the recording
  hold one (the list with the statements is
  /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/08-ik/analysis/underscore-statements.txt).
  For example:
  - s4_letters_0001.result:39, `SELECT tokenize('camelCaseWord PascalCase snake_case kebab-case
    SCREAMING_SNAKE_CASE', 'ik', <smart>) AS smart;` prints `{"tokens": [{"snake_case": 1},
    {"screaming_snake_case": 1}, {"camelcaseword": 1}, {"kebab-case": 1}, {"pascalcase": 1}],
    "doc_len": 5}`; mutated, `snake_case` and `screaming_snake_case` are gone and `snake`, `case` and
    `screaming` take their place.
  - s2_mixed_0001.result:135, `用户名admin_01登录失败了3次。` in smart mode prints `admin_01`;
    mutated, `admin` and `01`.
  - s6_edge_0001.result:211 and :219, `tokenize('a_', ...)` prints `{"tokens": [{"a_": 1}],
    "doc_len": 1}` and `["a_"]`; mutated, `a`.
  - s7_long_0001.result:221 and :225, row 20 (`snake_case_name ` 200 times): `snake_case_name` 200
    times in both modes; mutated, `snake`, `case` and `name`.
  - The index path: 14 boolean-mode MATCH queries whose term holds an underscore return a row in the
    reference and none mutated, because the index no longer holds the term, for example
    s8_index_0001.result:257-259, `SELECT id FROM ik_s8_doc WHERE MATCH(cs) AGAINST('snake_case' IN
    BOOLEAN MODE) ORDER BY id;` prints id 10. So does s6_edge_0004's
    `'snake_case' MEMBER OF (tokenize(c, 'ik'))`, which prints id 4 (s6_edge_0004.result:101-103).
    The natural-language queries do not change: their search text goes through the same mutated
    parser.
- So `compare` of a corpus recording made with the mutated binary against r04 reports 11 files
  `different` (s2_mixed_0001, s2_mixed_0002, s3_numbers_0001, s4_letters_0001, s4_letters_0002,
  s6_edge_0001, s6_edge_0003, s6_edge_0004, s7_long_0001, s8_index_0001, s8_index_0002) and the other
  11 identical, and `generate.py diff-tokens` names the statements (`tokens differ`).

## Why the 272 configured cases do not catch it

- Only fts_index.cn_word sends text through the IK parser (`git grep` over tools/deploy/mysql_test for
  `PARSER ik` and `'ik'` at 834bbee1e: cn_word.test, plus create_fts_index_afterward.test:406-408 and
  create_table_with_fts_index.test:291, which create IK indexes on empty tables, and
  tokenize_function.test:134, which fails on its properties before any text is segmented). None of
  cn_word's IK inputs has an underscore next to a letter or digit (its texts, its TOKENIZE literals
  and its natural-language search `hello-world`).
- In the coverage profile of the 272 cases
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb; both passes merged),
  `llvm-cov show` on unicode_utils.cpp gives `check_letter_connector` 52 calls, 12 of which returned
  true (line 69) and 40 false; line 68, the comparison, ran 332 times. The 40 false returns account
  for 280 comparisons (7 each), which leaves 52 for the 12 true returns. That fits 8 hits on `-` (the
  fourth entry, 4 comparisons each) and 4 on `.` (the fifth, 5 each), the only connectors in
  cn_word's IK inputs (`Hello-World`, `hello-world`, `1.2亿`); a hit on `_` would take 7. In
  ob_ik_letter_processor.cpp the connector branch (:176-177) ran 12 times.
- src/storage/fts and the IK parser are identical at 076eb309b and 834bbee1e (`git diff --stat
  076eb309b 834bbee1e -- src/storage/fts` prints nothing), so the profile's line numbers hold. A
  family 1 run of the mutated build is expected to pass.

## How the mutation stage runs it

Apply, rebuild and copy the binary aside as in ../README.md, "How to run them", then record the
corpus with the mutated binary the way families/ik/README.md records the reference, and compare it
with the C++ recording:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
A=/Users/colin/seekdb-dev/ref-archive-834bbee1e
RUN=<a new directory>
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $RUN/seekdb --obclient $A/client/obclient --mysqltest $A/client/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN/work --port <port> \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --init-sql $H/migration/judge/reduced-init/init.sql \
  --init-user-sql $H/migration/judge/reduced-init/init_user.sql \
  --test-dir $H/migration/judge/families/ik/cases --record-dir $RUN/rec
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/08-ik/r04/rec --right $RUN/rec
python3 $H/migration/judge/families/ik/generate.py diff-tokens \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/08-ik/r04/rec --right $RUN/rec
```

Caught when `compare` exits 1 with the 11 files above `different` (and a note that the seekdb binaries
differ). The run takes about 5 seconds including the instance start. `compare` refuses the pair if
cases/ changed after r04 (`test_dir_sha256`
47ca1ec946819c93ab1593cc09485083097452b6bf47a766e8dddf080438f753); then record the archived reference
again first. Revert the patch afterwards.

## Caught by (filled after the run)

Caught by family 13 on 2026-09-28 (README.md in this directory). The corpus recorded on the mutated
build, compared with 08-ik/r04, exits 1 with the 11 files named above `different` and the other 11
identical; `check-recording` finds 0 problems. `generate.py diff-tokens`: 38 token lists differ, each
because a token that held an underscore is split or cut (`snake_case` becomes `snake` and `case`,
`admin_01` becomes `admin` and `01`, `a_` becomes `a`), 17 more statements differ, 1,198 are
identical and none is missing. The 17 are the MEMBER OF query and 16 boolean-mode MATCH queries: the
14 whose term holds an underscore lose their row, and the two on the smart-mode column `cs` whose term
is `admin` or `snake`, a word that index now holds on its own, gain one (ids 9 and 10).

The 272 configured cases on the mutated build: only quarantined cases failed; fts_index.cn_word
passed. Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/mutations/08-ik-underscore-not-letter-connector/.

Run again on 2026-09-29, after the s8 searches gained the relevance column
(families/ik/README.md, "The relevance column (2026-09-28)"): the patch was built again in the
reference worktree (review/mutations/build.sh; seekdb sha256 0d8e58ee...; the worktree back to
reference-build.patch afterwards) and the corpus recorded on it (review/mut08-ik). Against the new C++
recording ik-r07, `compare` exits 1 with the same 11 files `different` and the other 11 identical.
`diff-tokens`: 1,162 statements identical, 38 token lists differ, 53 other statements differ: the
MEMBER OF query and 52 searches, 16 of them with other ids (as before) and 36 whose ids are the same
but whose relevance changed, because the words the index holds for the documents with underscores,
and so their `doc_len`, changed.
