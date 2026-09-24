# What item 8 (golden bytes) covers

PLAN.md section 4, item 8, lists "the CRC32C algorithm, zstd frames, and anything on the MySQL wire".
Decision 11 drops data-dir compatibility with the C++ build, so a byte format is a contract only if a
program outside the server reads or writes it. This note checks each named format against the source
at 834bbee1e (2026-09-24) and says where each one is judged.

## In scope

### Bytes on the MySQL wire

What the server sends for a fixed request, packet by packet, including what mysqltest and obclient
decode away: packet lengths and sequence ids, status flags and warning counts in OK and EOF packets,
column definitions (charset, length, type, flags, decimals), text rows, binary rows from
COM_STMT_EXECUTE, multi-result sets, packets of 16 MB or more split into 0xFFFFFF chunks, COM_PING,
COM_INIT_DB, COM_FIELD_LIST, COM_STMT_RESET and COM_STMT_CLOSE, COM_CHANGE_USER, ERR packets with
their SQLSTATE.

- **How:** a standalone script with its own minimal protocol client (Python standard library only)
  sends fixed request bytes, so the client side is identical in every run, and records the server's
  bytes per request. The two builds' recordings are compared byte for byte.
- **What varies between two runs of the same binary** is compared only by length and position, each
  field named in the script: the 20-byte auth-plugin-data (scramble) in the handshake, and the
  connection id if it is not the same on a fresh instance. Every other byte is exact.
- **The compressed protocol:** the server supports `CLIENT_COMPRESS` (rust/sql-nio/src/capability.rs:21
  and :40; src/oblib/rpc/obmysql/obsm_struct.h:72, `OB_MYSQL_COMPRESS_CS_TYPE`), whose frames are
  zlib streams. The script records compressed frames raw and exact. mysqltest has `-C/--compress`, so
  a runner option for it replays the 272 cases through the compressed protocol, the same way
  `--ps-protocol` does for item 4.

### SQL functions whose bytes come from an outside algorithm

- `CRC32()` is zlib's CRC-32, not CRC32C (src/sql/engine/expr/ob_expr_crc32.cpp, `crc32(0, buf, len)`).
  Known answer: `CRC32('123456789')` = 3421780262 (0xCBF43926).
- `COMPRESS()` writes a 4-byte length header and a zlib stream from zlib's `compress()`
  (src/sql/engine/expr/ob_expr_compress.cpp:81-104); `UNCOMPRESS()` and `UNCOMPRESSED_LENGTH()` read
  it back. Users store this output, so `HEX(COMPRESS(x))` must stay the same bytes; a Rust build needs
  a deflate implementation that gives zlib's exact output (a design-document choice).

These go into family 5's generator as known-answer vectors, next to its type matrix.

### Files written and read through SQL

- `SELECT ... INTO OUTFILE` with `COMPRESSION` = NONE, GZIP, DEFLATE or ZSTD writes through
  `ObCompressStreamWriter` (src/sql/engine/basic/ob_select_into_basic.cpp:29-67); ZSTD is the
  vendored zstd 1.3.8 (lib/compress/zstd_1_3_8). The judge compares the written files byte for byte,
  and also decompresses them with the system tools (gzip, zstd 1.5.7 from Homebrew) to check that
  they are valid frames.
- `LOAD DATA` reads files compressed as GZIP, DEFLATE or ZSTD, or AUTO by name
  (src/sql/engine/cmd/ob_load_data_parser.cpp:442-475; ob_load_data_file_reader.cpp:690-724). The
  judge loads files made by the system tools and compares the loaded rows.

## Out of scope: moved to the core build's unit tests

- **CRC32C** (and crc64): no SQL function and no protocol field uses it (no match under src/sql,
  src/observer, src/oblib/rpc/obmysql or rust/sql-nio). It checksums clog entries, macro and micro
  blocks inside the data dir, which Decision 11 does not keep compatible.
- **zstd, lz4 and snappy table compression** (`COMPRESSION='zstd_1.3.8'` in DDL): the option text is
  visible in `SHOW CREATE TABLE` and judged there by family 1; the compressed blocks stay inside the
  data dir.
- OB_UNIS, ObNumber and JSON binary, as PLAN.md item 8 already says (Decision 11).

## What this changes in the plan

Item 8's list becomes: the MySQL wire (including compressed frames), `CRC32()` and `COMPRESS()`
output, and the files `SELECT ... INTO OUTFILE` writes and `LOAD DATA` reads. CRC32C leaves the
judge. Each piece still needs a caught mutation at the second sign-off (PLAN.md section 4, "00b's
exit").
