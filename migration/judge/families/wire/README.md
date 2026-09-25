# Item 8: golden bytes on the MySQL wire and in files

PLAN.md section 4, item 8, as scoped by migration/judge/golden-bytes-scope.md: the bytes a client
sees on the MySQL wire, including compressed-protocol frames, and the files `SELECT ... INTO OUTFILE`
writes and `LOAD DATA` reads. `wire_scenarios.py` runs ten scenarios against one seekdb binary and
records them in the runner's recording format (`DIR/<scenario>.result` and `DIR/manifest.json`, as
restart_scenarios.py does), so the runner's `compare` diffs two builds unchanged. `wire_client.py` is
its minimal MySQL protocol client. `known-answers.sql` holds the `CRC32()`, `COMPRESS()`,
`UNCOMPRESS()` and `UNCOMPRESSED_LENGTH()` vectors for family 5.

**Not in this directory:** item 8's `--compress` replay of the 272 configured cases is the runner's
`--compress` option (it passes mysqltest's `-C/--compress`), applied to the live runner on 2026-09-25
with the rest of migration/judge/harness/second-set/runner-second-set.patch.

## What each scenario checks

Every scenario starts a fresh instance through sdb.py (start, wait-ready, init), runs, checks that
the server is still running, and destroys the instance, exactly as restart_scenarios.py does. All
requests are fixed bytes; the client logs in as `root` with an empty password unless a step says
otherwise. Every connection ends with COM_QUIT (or a login failure) followed by a check that the server
closes it without sending anything more, so a stray packet after the last response fails the scenario.
Each scenario also opens one unrecorded probe connection before its first step, and checks two things
at its end (see "Checks on the masked fields").

| Scenario | What it sends |
|---|---|
| `wire_handshake` | The greeting and login OK under the default capabilities; logins without CONNECT_WITH_DB, with the minimal 4.1 capabilities (then a multi-statement query), with PLUGIN_AUTH_LENENC_CLIENT_DATA, with client charsets 63 and 33; failed logins (unknown user 1045, unknown database 1049, wrong password 1045); `caching_sha2_password` named by the client for `root` (no switch) and for `admin` (an auth switch the client answers with the native-password reply); `admin` with the correct and a wrong native reply; an SSL request although the greeting offers no TLS (1043); a handshake response with sequence id 5; a 10-byte handshake response. After each failure, whether the server closes the connection |
| `wire_ok_err_eof` | OK packets with affected rows, info strings, last insert ids and warning counts; ERR packets 1062, 1146, 1064, 1054, 1364, 1048, 1406 with their SQLSTATE; EOF after column definitions and rows; `SHOW WARNINGS`; status flags through BEGIN/COMMIT, autocommit off and ROLLBACK, a read-only transaction, NO_BACKSLASH_ESCAPES, `USE`; a second connection with FOUND_ROWS and without SESSION_TRACK |
| `wire_text_types` | One table per column type seekdb accepts (68 cases, below), each with typical and edge values and a NULL row, read back with the text protocol; the DATE and DATETIME tables also hold the zero date, and the DATETIME(6) and TIME(6) tables a value whose fraction is zero; five SELECTs of expression-only types (NULL, hex and bit literals, big integer and decimal literals, date/time literals and casts, CONVERT, JSON, geometry) |
| `wire_binary_types` | The same tables and SELECTs through COM_STMT_PREPARE and COM_STMT_EXECUTE (binary rows, with and without NULLs; the zero dates give binary length 0, a zero fraction length 7 or 8); a 14-parameter SELECT executed with types, with the types not sent again, and with every value NULL; an INSERT with parameters (OK, and 1062); COM_STMT_SEND_LONG_DATA; a cursor (CURSOR_TYPE_READ_ONLY) read with COM_STMT_FETCH, reset and fetched again; unknown statement ids; a prepare syntax error; the same text prepared twice; and on a new connection 50 prepared statements (the default `open_cursors`), the 51st failing with 5930, then one closed and the 51st prepared again |
| `wire_multi_results` | Multi-statement queries (two SELECTs; DDL, DML and a SELECT; an error in the middle; a trailing semicolon; a transaction), stored procedures returning two result sets, failing in the middle, and with an OUT parameter; CALL through the binary protocol; `CALL p_out(?)` prepared and executed, whose OUT value comes back as a result set that must carry PS_OUT_PARAMS (0x1000); a connection without MULTI_RESULTS and PS_MULTI_RESULTS |
| `wire_large_packets` | `@@max_allowed_packet` (16777216 by default); text rows whose payload is 0xFFFFFE, exactly 0xFFFFFF (two packets, the second empty), 0x1000000 and 16777225 bytes; REPEAT one byte past the limit (1301); a two-column row of 16 MB; a 16 MB + 86 byte query (1153); then `SET GLOBAL max_allowed_packet = 67108864`, a wait until new connections see it, and on a new connection a row of exactly 2 x 0xFFFFFF bytes (three packets), the 16 MB query accepted, and a binary row of exactly 0xFFFFFF bytes |
| `wire_commands` | COM_PING; COM_INIT_DB (OK, 1049, information_schema); COM_FIELD_LIST (all columns, a wildcard, an unknown table); COM_STMT_RESET (valid and unknown id); COM_STMT_CLOSE (valid and unknown id, each followed by a PING, since CLOSE has no answer); EXECUTE of a closed statement; COM_RESET_CONNECTION; COM_SET_OPTION off and on around a multi-statement query; COM_TIME, which the server does not implement (1235); COM_STATISTICS (a fixed string); COM_DEBUG (an EOF); COM_REFRESH with REFRESH_GRANT (OK); COM_PROCESS_KILL of connection id 2000000000, which no connection has (1094); COM_CHANGE_USER to `root`, to `admin` through the auth switch, and to `admin` with a wrong reply (1045, then the close); on a connection without PLUGIN_AUTH, COM_CHANGE_USER answered directly |
| `wire_compressed` | The same kinds of requests on a CLIENT_COMPRESS connection: PING, small results (frames sent uncompressed), larger results (deflated frames), a request the client deflates, multi-results, an ERR, binary rows, INIT_DB, FIELD_LIST, a 100 KB result spread over several server batches, 200 rows of `md5(n)` (one batch) and 1000 rows of `md5(n)` and `sha2(n, 256)` (several batches), so the deflated frames also carry text that does not repeat, the text-protocol SELECTs of nine type tables, a 16 MB row (frames of at most 0xFFFFFF plain bytes), COM_CHANGE_USER through compressed frames |
| `file_outfile` | A fixed 300-row table (NULLs, tab, newline, backslash, quotes, UTF-8) exported with `FORMAT = (TYPE = 'CSV', COMPRESSION = ...)` as NONE, GZIP, DEFLATE and ZSTD; the files' bytes; checks that `gzip -dc` (GZIP and DEFLATE) and `zstd -dcq` (ZSTD) give the NONE file; a 10000-row table exported as NONE, GZIP and ZSTD, each also with `BUFFER_SIZE = 4096`, with the same checks; an empty table in all four; errors (the file exists, the directory is missing, COMPRESSION 'LZ4'); and each of the four files loaded back with COMPRESSION 'AUTO' into a copy whose rows must equal the table's |
| `file_load_data` | A fixed 60-row CSV (enclosed fields, escapes, NULLs, an empty string, UTF-8) loaded as NONE, with no COMPRESSION clause, GZIP (`gzip -n -6`), DEFLATE (a zlib stream at level 6, and the gzip file), ZSTD (`zstd -3 -T1`), and AUTO for .gz, .deflate, a gzip file named .deflate, .zst, .zstd and .csv, plus two concatenated gzip members; after each load the rows, which must equal the plain load's; then COMPRESSION 'LZ4', a plain file as GZIP, a gzip file as ZSTD, and a truncated gzip file, each into its own table; `LOAD DATA LOCAL INFILE` on a connection without CLIENT_LOCAL_FILES (3948); and on connections with CLIENT_LOCAL_FILES, LOCAL loads that the client answers with the CSV in 512-byte packets, with the CSV gzip-compressed (COMPRESSION 'GZIP'), and with an empty file, the first also on a compressed connection |

The 68 type cases come from the MySQL-mode `data_type` rule (src/sql/parser/sql_parser_mysql_mode.y:5323,
and `int_type_i` :5767, `float_type_i` :5775, `datetime_type_i` :5797, `date_year_type_i` :5803,
`text_type_i` :5808, `blob_type_i` :5817, BIT :5633, ENUM :5645, SET :5653, JSON :5661, GEOMETRY and
its subtypes from :5666, ARRAY :5715, VECTOR :5725, MAP :5730, SPARSEVECTOR :5735), cross-checked
with the table that maps every ObObjType to a MySQL field type
(src/query/protocol/ob_mysql_protocol_util.cpp:44-91): signed, unsigned and zerofill integers, BOOLEAN,
FLOAT and DOUBLE (plain, unsigned, with (M,D)), DECIMAL and NUMERIC (including (65,30) and an
integer-only precision), DATE, DATETIME, DATETIME(6), TIMESTAMP, TIMESTAMP(3), TIME, TIME(6), YEAR,
CHAR, VARCHAR (utf8mb4 with 2-, 3- and 4-byte characters, latin1, gbk), NCHAR, NVARCHAR, BINARY,
VARBINARY, the four TEXT and four BLOB sizes, BIT(1), BIT(12), BIT(64), ENUM, SET, JSON, the eight
geometry types, VECTOR(3), ARRAY(INT), ARRAY(VARCHAR), INT[], MAP(INT, INT) and SPARSEVECTOR. The
expression SELECTs reach the types no column has (ObNullType, ObHexStringType) and the literal forms
of the temporal and decimal types.

## Command line

```
cd /Users/colin/seekdb-dev/migrate-to-rust
python3 migration/judge/families/wire/wire_scenarios.py \
  --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
  --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --base-dir /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-a/base \
  --record-dir /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-a/rec \
  --port 3891 \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql \
  --gzip /usr/bin/gzip --zstd /opt/homebrew/bin/zstd

python3 .github/script/seekdb/mysqltest_for_seekdb.py compare --left <rec A> --right <rec B>
```

| Option | Default | What it does |
|---|---|---|
| `--seekdb FILE` | required | The server binary |
| `--obclient FILE` | required | Used only by `sdb.py wait-ready` and the runner's init; it also fills the manifest's `mysqltest` fields, as in restart_scenarios.py. The scenarios themselves use wire_client.py |
| `--base-dir DIR` | required | New or empty; `sdb.py destroy` removes it after each scenario |
| `--record-dir DIR` | required | New or empty; gets `manifest.json` and one `<scenario>.result` (or `.partial`) per scenario |
| `--port N` | required | The server's SQL port; use 3891-3899 (3881 and 3882 are the judge's) |
| `--init-sql FILE`, `--init-user-sql FILE` | tools/deploy/init.sql, init_user.sql | As in the runner. Judge runs pass the reduced init, which creates the `admin` user (password `admin`) that several steps log in as |
| `--scenario NAME` | all | Repeatable; scenarios always run in the fixed order |
| `--file-dir DIR` | /tmp/seekdb-judge-wire-files | The directory the file scenarios name in their SQL. It must not exist; the script creates it, moves each file scenario's files into the work directory afterwards, and removes it at the end. Its path is part of the recorded SQL, so two recordings compare cleanly only when both used the same value; two runs at the same time need different values and are then not comparable with each other |
| `--gzip FILE`, `--zstd FILE` | the first on the PATH | Needed only by the file scenarios. Pass them explicitly: this machine has two zstd binaries on the PATH (Homebrew and miniconda), and which one comes first depends on the shell |
| `--save-instance-dir DIR` | `$SEEKDB_COV_PROFRAW_DIR` | As in the runner and restart_scenarios.py |

Exit code 0 means every requested scenario ran and every check and expectation held; 1 means anything
else; argument errors exit 2.

## The recording

manifest.json has the keys restart_scenarios.py writes (its section "The recording" explains how
`compare` uses them): `cases` are the scenario names, `max_retries` 0, `fresh_instance_per_case` true,
`runner_sha256` is this script's sha256, and `mysqltest_runner_sha256` the runner's. It adds
`client_sha256` (wire_client.py), `file_dir`, `gzip`, `gzip_version`, `gzip_sha256`, `zstd`,
`zstd_version`, `zstd_sha256` and `python_zlib_version`; `compare` ignores them, they are there to
explain an input difference.

Each `<scenario>.result` starts with the scenario name, `-- recorder sha256`, `-- client sha256` and
the lifecycle line, so any change to either script shows in every scenario and means recording the C++
reference again. Then, in order:

| Line | Meaning |
|---|---|
| `-- connection N: ... (expect X)` | A new connection: user, database, capabilities (hex and names), charset, plugin, and the outcome the step expects |
| `-- request N on connection M: ... (expect X)` | A request, described (SQL text escaped, cut after 300 bytes), with its expected outcome: `ok`, `rows`, `results:N`, `err:CODE`, `err` (any code), `prepare`, `cursor`, `eof`, `string`, `none` (no answer), `closed`, or `anything` |
| `C <seq> <length> <hex>` | A packet the client sent, one line per wire packet; the client's answer to a LOCAL INFILE request follows the server packet that asked for it |
| `-- client frame: ...` | On a compressed connection, how the client framed its packets: every frame of at least 50 plain bytes is deflated, shorter ones are sent as they are. The rule depends only on the length, so the line is the same whatever zlib the client's Python has; the deflated bytes themselves are not recorded |
| `Z <seq> <compressed length> <uncompressed length> <hex>` | A compressed frame from the server exactly as received; the uncompressed length is 0 for a frame sent as is |
| `S <seq> <length> <hex>` | A server packet, one line per wire packet, so a 16 MB row is several lines; on a compressed connection these are the packets inside the frames, after the response's `Z` lines |
| `. <text>` | A summary decoded from the recorded bytes of the logical packet just shown (handshake, OK, ERR, EOF, column count, column or parameter definition, text or binary row, prepare OK, auth switch request, LOCAL INFILE request, string) |
| `F <offset> <hex>` | Bytes of a file, 32 per line, after `-- file NAME: N bytes` |
| `-- input NAME: HOW; N bytes, sha256 X` | A file the server reads that was made by an outside tool or by Python's zlib (file_load_data). Its bytes can change with the tool, so its length and sha256 are part of the recording: a changed tool shows as a changed input line, not as a changed server answer |
| `-- check: ...: yes` or `: no` | A check; `no` fails the scenario. An expectation that does not hold adds `-- check: request N ended as X, expected Y: no` |

A payload, frame body or file over 65536 bytes is written as `sha256=<hex> head=<first 64 bytes>
tail=<last 64 bytes>`: the comparison stays byte for byte through the digest, only the dump is
shortened.

Nothing that changes between two runs of the same binary is written, except as a named placeholder
that stands for exactly as many bytes as it replaces:

| Placeholder | Where | Why it varies (the source) |
|---|---|---|
| `{connection_id:4}` | The greeting | The session id comes from one server-wide counter whose first id is 2 (0 and 1, the inner SQL session's id, are skipped) and which skips ids still in use (src/sql/session/ob_sql_session_mgr.cpp:165, :237-259), and every accepted connection takes one when it is created, before login (src/observer/mysql/obsm_conn_callback.cpp:55-68; put in the greeting at src/oblib/rpc/obmysql/ob_sql_nio_server.cpp:49). sdb.py wait-ready's obclient attempts and the init sessions take ids too, and so do inner SQL sessions and background work on their own schedule (src/observer/ob_inner_sql_connection.cpp:1367, src/observer/dbms_scheduler/ob_dbms_sched_job_executor.cpp:170, src/rootserver/truncate_info/ob_truncate_info_service.cpp:255, src/sql/engine/ob_des_exec_context.cpp:84, :100). So the id is not the same on a fresh instance |
| `{scramble:8}`, `{scramble:12}` | The greeting's two auth-data parts | `scramble_buf_` is 21 bytes (src/oblib/rpc/obmysql/obsm_struct.h:111); it is filled with 20 random characters from 33 to 126 and a zero byte (obsm_conn_callback.cpp:84; src/oblib/lib/random/ob_mysql_random.h:92-100, seeded from a server-wide random value and the clock at obsm_conn_callback.cpp:42-44). The greeting carries the 20 characters as 8 + 12 bytes (ob_sql_nio_server.cpp:50; rust/sql-nio/src/handshake.rs:35, :43) and then its own zero terminator (:44), which stays visible |
| `{scramble:20}` | Auth switch requests | The login and COM_CHANGE_USER send the whole 21-byte buffer (src/observer/mysql/obmp_connect.cpp:388, src/observer/mysql/obmp_change_user.cpp:112): the 20 characters are masked, the zero byte stays visible |
| `{auth_response:20}` | `C` lines only | The client's native-password reply for `admin` is computed from the scramble. Every other request, including every `root` login, is the same bytes in every run |
| `{unparsed_handshake:N}` | A greeting the client cannot parse | Everything after the protocol byte, because the client cannot find the connection id and scramble in it (wire_client.py `read_greeting`). The scenario stops there, so this appears only in a `.partial`; `compare` still diffs two `.partial` files (its `failed_alike`), and the mask keeps two failures of one binary alike |

A compressed frame whose body is deflated and holds masked bytes would be written as
`{compressed_length}` and `{deflated_body_with_scramble}`; with the reference this cannot happen,
because an auth switch request is 48 bytes and frames under 50 bytes are sent uncompressed
(rust/sql-nio/src/compress.rs:20, :86).

### Checks on the masked fields

The masks hide values; these checks, which record no values, make sure a build still fills them in:

- **Every scramble** is 20 characters from 33 to 126, and every auth switch request carries its
  connection's greeting scramble.
- **The connection id is the session's id.** Before each scenario's first step, an unrecorded probe
  connection logs in and runs `SELECT CONNECTION_ID()`; the result must equal the id in the probe's
  greeting. `CONNECTION_ID()` returns `get_sid()` (src/sql/engine/expr/ob_expr_connection_id.cpp:47-57),
  which is the `sessid_` the session was created with from `conn.sessid_` (src/sql/session/ob_basic_session_info.h:577;
  ob_sql_session_mgr.cpp:277-303), the value the greeting carries (ob_sql_nio_server.cpp:49). A build
  that sends 0 or a fixed id fails this check.
- **Scrambles and ids differ per connection.** At the end of each scenario, the scrambles of all its
  recorded greetings must be pairwise different, and so must their connection ids (the counter only
  goes up and skips ids in use, ob_sql_session_mgr.cpp:237-259). A build that reuses one scramble or
  one id fails. The check line gives the number of greetings, which is fixed by the script.

### Fields recorded exactly

Checked in the source to be the same in every run: the greeting's version, capabilities, charset and
status (ob_sql_nio_server.cpp:53-59; rust/sql-nio/src/handshake.rs:17-21;
rust/sql-nio/src/capability.rs:39-40); prepared statement ids, which count per session from 1
(src/sql/session/ob_sql_session_info.cpp:884); ERR messages, which carry a time and a trace id only when
`enable_rich_error_msg` is on (default false, src/share/parameter/ob_parameter_seed.ipp:122), and the
KILL error, which names the fixed id the client sent; the slow-query trace id, which only goes to
`session.set_last_trace_id`, never into the OK packet (src/observer/mysql/obmp_packet_sender.cpp:763-775);
the LOCAL INFILE file name, which is the text of the statement; and compressed frame boundaries, which
follow the server's response batches (13312 bytes, rust/sql-nio/src/lib.rs:91;
rust/sql-nio/src/response_api.rs:70, :134-137) and its explicit flushes (the last one of a response,
the login's auth switch, and the LOCAL INFILE request,
src/sql/engine/cmd/ob_load_data_file_reader.cpp:297), all decided by the response content, not by
timing.

## Source facts the scenarios rely on

- **Login for `root` with an empty password.** The greeting names `mysql_native_password` with 21 bytes
  of auth data (rust/sql-nio/src/handshake.rs:19, :21). The server takes the handshake response's auth
  data as a native-password reply (src/observer/mysql/obmp_connect.cpp:374); an account with an empty
  password gets no auth switch whatever plugin the client names (:376-379), and an empty reply matches
  an empty stored password without any hashing (src/share/schema/ob_schema_getter_guard.cpp:1317-1319).
  So the client sends an empty auth response with plugin `mysql_native_password`, and the request bytes
  are fixed. A non-empty password is checked with the standard SHA1 exchange over the first 20 scramble
  bytes and needs a 20-byte reply (src/oblib/lib/encrypt/ob_encrypted_helper.cpp:246-272); for another
  plugin name the server sends an auth switch to `mysql_native_password` (obmp_connect.cpp:377-438).
- **Capabilities.** The server offers 0x009FF7FF, which includes LOCAL_FILES and COMPRESS
  (rust/sql-nio/src/capability.rs:39-40), and gives the session the client's flags plus
  MULTI_STATEMENTS (:54-56; rust/sql-nio/src/pump.rs:268). The client sends 0x009FA20D by default
  (wire_client.py `DEFAULT_CAPABILITIES`), a subset of what the server offers, with charset 45 and two
  fixed connection attributes.
- **Compressed protocol.** The login and its OK are plain (rust/sql-nio/src/pump.rs:388); compression
  starts with the first command. One compressed sequence counts through a whole request in both
  directions: each frame the server reads must carry the next number (pump.rs:578-593), each frame it
  sends takes the next one (response_api.rs:30), and the count restarts at 0 when the request ends,
  except during a change-user auth switch (rust/sql-nio/src/request.rs:783-791). So during a LOCAL
  INFILE load the client's frames continue the numbers of the server's. Frames are made per published
  batch, split at 0xFFFFFF plain bytes, deflated with flate2's default level when at least 50 bytes and
  smaller (rust/sql-nio/src/compress.rs:20-22, :46-105). A frame the server reads may be deflated at
  any size, as long as it inflates to exactly its stated length (:117-176). The reference links flate2
  1.1.10 with the miniz_oxide backend (rust/Cargo.lock; the reference's build_release/rust-target), not
  zlib, so a Rust build matches these frames only with the same deflate implementation, level and
  batch boundaries.
- **Large packets.** A payload of exactly 0xFFFFFF bytes is followed by an empty packet
  (rust/sql-nio/src/response.rs:900-911). Responses may be up to 1 GB (rust/sql-nio/src/packet.rs:19);
  `max_allowed_packet` is 16777216 by default and read-only per session
  (src/share/system_variable/ob_system_variable_init.json:238-249), limits REPEAT
  (src/sql/engine/expr/ob_expr_repeat.cpp:127-129, :260; error 1301, src/share/ob_errno.def:749) and
  requests (src/observer/mysql/obmp_query.cpp:101-104; error 1153, ob_errno.def:283).
- **Binary temporal values.** A DATE is sent with length 4, or 0 for the zero date; a DATETIME or
  TIMESTAMP with length 11 when it has microseconds, 7 when it has a time of day, else 4, or 0 for the
  zero date; a TIME with length 12, 8, or 0 for 00:00:00 (rust/sql-nio/src/response.rs:298-324,
  :681-725). The C++ fills the parts from the value (src/query/protocol/ob_mysql_protocol_util.cpp:176-186,
  :398-411). The default `sql_mode` lacks NO_ZERO_DATE, so '0000-00-00' and '0000-00-00 00:00:00' are
  accepted (src/oblib/common/timezone/ob_time_convert.cpp:3946-3962).
- **The cursor limit.** COM_STMT_PREPARE fails with -5930 once the session holds `open_cursors`
  prepared statements (src/sql/ob_sql.cpp:1381-1387; default 50,
  src/share/parameter/ob_parameter_seed.ipp:785); the error has no MySQL number, so the packet carries
  5930 (ob_errno.def:1296; src/share/ob_errno.cpp:15614-15620).
- **A prepared CALL with an OUT parameter** answers with a result set holding the OUT values: the column
  definitions end with an EOF whose status has PS_OUT_PARAMS (src/observer/mysql/ob_sync_cmd_driver.cpp:220;
  src/observer/mysql/ob_query_driver.cpp:113), then the row, then an EOF sent in place of the final OK
  (ob_sync_cmd_driver.cpp:175; obmp_packet_sender.cpp:826-831).
- **COM_CHANGE_USER** always answers a client with PLUGIN_AUTH with an auth switch
  (src/observer/mysql/obmp_change_user.cpp:69-70, :108-117) and checks the reply in
  src/observer/mysql/obmp_auth_response.cpp; a failure disconnects. **COM_FIELD_LIST** runs through the
  query path (src/observer/ob_srv_xlator.cpp:210-226) and sends no column-count packet
  (src/observer/mysql/ob_query_driver.cpp:56-121). An SSL request is refused with 1043 when TLS is off
  (rust/sql-nio/src/tls.rs:176-181; rust/sql-nio/src/pump.rs:429-442; `ssl_client_authentication` is
  false by default, ob_parameter_seed.ipp:739).
- **The other commands** (src/observer/ob_srv_xlator.cpp:145-190). COM_STATISTICS answers with the fixed
  string "Active threads not support" (src/observer/mysql/obmp_statistic.cpp:37-40), COM_DEBUG with an
  EOF (obmp_debug.cpp:56), COM_REFRESH with an OK whatever its sub-command (obmp_refresh.cpp:53), and
  COM_PROCESS_KILL runs `KILL <id>` through the query path (obmp_process_kill.cpp:41-53), which answers
  1094 "Unknown thread id: <id>" for an id no session has (src/sql/engine/cmd/ob_kill_executor.cpp:28-48;
  ob_errno.def:214). The payload sizes are fixed: none for STATISTICS and DEBUG, one byte for REFRESH,
  four for PROCESS_KILL (rust/sql-nio/src/command.rs:229-259); any other size closes the connection
  (pump.rs:469-474). COM_PROCESS_INFO is not sent: it runs SHOW PROCESSLIST
  (obmp_process_info.cpp:41), whose rows hold session ids, the client's host and port, and times.
- **SELECT ... INTO OUTFILE.** COMPRESSION is set only inside `FORMAT = (...)`
  (src/sql/parser/sql_parser_mysql_mode.y:8911, :7350) with NONE, GZIP, DEFLATE, ZSTD or AUTO
  (src/sql/engine/cmd/ob_load_data_parser.cpp:454-476). SINGLE is true by default, so the file gets
  exactly the given name (src/sql/resolver/dml/ob_select_stmt.cpp:79;
  src/sql/engine/basic/ob_select_into_op.cpp:181); an existing file is an error because the writer
  creates it (src/sql/engine/basic/ob_external_file_writer.cpp:32) through `IFileAppender::create`,
  which adds O_EXCL (src/oblib/lib/file/ob_file.cpp:372-376). GZIP and DEFLATE both write a gzip
  stream with zlib at level 5 (src/sql/engine/basic/ob_select_into_basic.cpp:159-163, :258-259). ZSTD
  writes frames of the vendored zstd 1.3.8 at level 3, with no content size and no checksum: the
  context is created at level 1 (src/oblib/lib/compress/zstd_1_3_8/ob_zstd_wrapper.cpp:33, :141-142),
  but that only sets the parameters of that one `ZSTD_compressBegin`; the stream calls
  (`ZSTD_compressStream2`, :309-324) start from the context's requested parameters, which creating the
  context reset to `ZSTD_CLEVEL_DEFAULT`, 3 (zstd_src/zstd_compress.c:53-62, :186-189, :822-834,
  :4019-4028; zstd_src/zstd.h:89), and the first call is `ZSTD_e_continue`
  (ob_external_file_writer.cpp:139-149; src/sql/engine/basic/ob_select_into_basic.h:108), so the size is not known when the frame header is written.
  The compressed output is flushed in buffers of BUFFER_SIZE clamped to 4 KB-1 MB
  (ob_select_into_basic.cpp:29-30, :113-120; ob_select_into_op.cpp:1005-1008), and the compressor is
  created at the first row, so an empty result may give an empty file. zlib is 1.2.13, linked
  statically from deps/3rd (ref-archive-834bbee1e/deps-3rd/usr/local/oceanbase/deps/devel/lib/libz.a).
- **Paths.** The directory of an OUTFILE and every LOAD DATA file go through realpath and
  `secure_file_priv` (ob_select_into_op.cpp:1201-1227; src/sql/resolver/cmd/ob_load_data_resolver.cpp:651-661;
  src/sql/resolver/ob_resolver_utils.cpp:6048-6110), whose default `/` allows every path
  (src/share/system_variable/ob_system_variable_init.json:2449-2461). Relative paths would depend on
  the server's working directory, so the scenarios use absolute paths under `--file-dir`.
- **LOAD DATA.** The COMPRESSION clause comes after the table name
  (src/sql/parser/sql_parser_mysql_mode.y:4127, :4192-4201); without it the file is read as NONE
  (src/sql/resolver/cmd/ob_load_data_stmt.h:61); AUTO is resolved from the suffix: .gz, .deflate, .zst
  or .zstd, anything else NONE (src/sql/engine/cmd/ob_load_data_file_reader.cpp:48-60;
  ob_load_data_parser.cpp:503-515). GZIP and DEFLATE share one inflater opened with
  `inflateInit2(32 + MAX_WBITS)`, which accepts a gzip or a zlib header and restarts after each gzip
  member (ob_load_data_file_reader.cpp:436-440, :616, :664); ZSTD uses the vendored zstd 1.3.8
  (:441-443, :679-746).
- **LOAD DATA LOCAL INFILE** needs both the `local_infile` variable (on by default,
  ob_system_variable_init.json:889-897) and the client's CLIENT_LOCAL_FILES, or it fails with 3948
  (ob_load_data_resolver.cpp:127-135, :1229-1240; ob_errno.def:1818; src/share/mysql_errno.h:1031).
  The server sends 0xFB and the file name and flushes (ob_load_data_file_reader.cpp:283-311), then
  reads the client's packets until an empty one (:356-390); the decompression reader wraps this reader
  too (:66-106), so COMPRESSION applies. LOCAL implies IGNORE for duplicate keys
  (ob_load_data_resolver.cpp:403-410).
- **CRC32() and COMPRESS().** `CRC32()` is zlib's `crc32(0, buf, len)` returning an unsigned 64-bit value
  (src/sql/engine/expr/ob_expr_crc32.cpp:30-58). `COMPRESS()` returns the empty string for empty input,
  otherwise a 4-byte little-endian length masked with 0x3FFFFFFF followed by zlib `compress()` output at
  the default level; it allocates one byte more but never writes it, so unlike MySQL it appends no '.'
  after output that ends in a space (src/sql/engine/expr/ob_expr_compress.cpp:70-111). `UNCOMPRESS()`
  returns the empty string without a warning for an empty argument, and NULL with a warning for an
  argument of 1 to 4 bytes, for a length over `max_allowed_packet` and for data zlib rejects (:150-236).
  A length header of 0 returns the empty string without inflating (:201, :230-231); a header larger
  than the stream returns what the stream holds (:206-215); a smaller one runs out of room and returns
  NULL with a warning. `UNCOMPRESSED_LENGTH()` returns 0 without a warning for an empty argument, 0 with
  a warning for 1 to 4 bytes, and otherwise the header (:266-287).
- **Defaults that the expected outcomes assume:** `sql_mode` 281018368, which is STRICT_ALL_TABLES,
  NO_ZERO_IN_DATE and NO_AUTO_CREATE_USER (src/share/system_variable/ob_system_variable_init.json:257;
  src/oblib/common/sql_mode/ob_sql_mode.h:47-53), so SELECT 1/0 gives no error and zero dates are
  accepted; `time_zone` +08:00 (ob_system_variable_init.json:276); `autocommit` on.

## Known answers for family 5

`known-answers.sql` is in mysqltest syntax, for family 5's generator. Each CRC32 line prints the value
and whether it equals the CRC-32 check value (3421780262 for '123456789') or a value from zlib's
`crc32`. Each short COMPRESS line prints `HEX(COMPRESS(x))` and whether it equals the expected bytes,
for empty, one-byte, short, trailing space, binary, 1 KB, 3 KB and 3.6 KB inputs and x'1f', whose
output ends in 0x20 (the case where MySQL would append '.'). Four long inputs are built in SQL from a
number sequence (the file creates and drops `ka_digits` and `ka_seq` and raises
`group_concat_max_len` for the session, then sets it back): 102400 bytes and 960000 bytes of `md5(n)`
hex text, 134032 bytes of text of pseudo-random words chosen by the digits of `md5(n)`, and 131072
bytes of `unhex(sha2(n, 256))`, which does not compress and so goes through stored blocks. For each
the file prints the length, whether the input's md5 is the expected one (so a wrong input is not taken
for a wrong deflate), the length and md5 of `COMPRESS(x)`, whether that md5 is the known answer, and
in a separate statement whether the header matches and `UNCOMPRESS()` gives the input back.
`UNCOMPRESS()` and `UNCOMPRESSED_LENGTH()` also read known bytes back, and the NULL, warning and
header-mismatch paths above each have a line, followed by `SHOW WARNINGS`.

The expected bytes come from the reference's own zlib: a small C program (/tmp/wire-offline/zlib1213/ka.c)
linked against ref-archive-834bbee1e/deps-3rd/usr/local/oceanbase/deps/devel/lib/libz.a (1.2.13)
computed every answer, and Python's zlib 1.2.12 and obd's zlib 1.3.1 give the same bytes for all of them. What the
vectors tell apart, measured on the deflate body (without the 2-byte zlib header and the checksum):

| Deflate differing from zlib 1.2.13 at level 6 | Short vectors (8 old) that differ | New vectors (x'1f' and 4 long) that differ |
|---|---|---|
| miniz_oxide 0.9.1 at level 6 (flate2's default backend) | 3 | the 4 long ones |
| zlib-rs 0.6.8 at level 6 | 3 | the 4 long ones |
| zlib 1.2.13 with memLevel 1-7 or 9 | none | the 4 long ones |
| zlib 1.2.13 with Z_FILTERED | none | the 4 long ones |
| zlib 1.2.13 levels 1-3 | 3 | the 4 long ones |
| zlib 1.2.13 level 4 | none | both hex texts and the word text |
| zlib 1.2.13 levels 5, 7, 8, 9 | none | the word text |
| zlib 1.2.13 with Z_HUFFMAN_ONLY, Z_RLE or Z_FIXED | 2 or 3 | 3 or 4 |

A different level also changes the zlib header byte, so whole outputs differ on every vector; the table
is for a deflate that writes the level-6 header but compresses differently. So a Rust `COMPRESS()` passes
the long vectors only with a deflate that makes zlib 1.2.13's exact block and match decisions.

`UNCOMPRESS()` hands zlib the whole argument length but starts 4 bytes in, so zlib may read up to 4
bytes past the end of the argument (ob_expr_compress.cpp:206-207, :218-219). An answer that depended
on those bytes would not be reproducible, so every `UNCOMPRESS()` vector is a complete stream, a stream
zlib rejects at its 2-byte header, a header that stops inflation before the stream ends, or an argument
rejected before inflation; a truncated stream must not be added.

## What was checked offline

Nothing here has run against a server (injected-mutation judge runs occupy this machine).

- `python3 -m py_compile` of both scripts and `--help`.
- An in-process test harness (kept outside the repo, /tmp/wire-offline/fake_server.py and
  run_tests.py) with a fake MySQL server on socket pairs that follows the source where it matters: a
  greeting laid out as handshake.rs builds it, 20 random scramble characters and a random connection id
  per connection, `SELECT CONNECTION_ID()` answered with the greeting's id, an auth switch carrying the
  21-byte buffer, native-password checks, login ERR followed by a close, the SSL refusal, compressed
  frames per batch with deflate from 50 bytes and one sequence count through a request, 0xFFFFFF
  splitting with the trailing empty packet, OUTFILE files written as gzip and zstd, LOCAL INFILE (3948
  without CLIENT_LOCAL_FILES, 0xFB and the client's packets otherwise, gzip included), the four new
  commands, and the PS_OUT_PARAMS EOF; the answer to each request follows the expectation the script
  itself declares. Results, 55 of 55 checks:
  - All ten scenarios pass twice and `compare` finds the two recordings identical (10 of 10, no
    recording problems). No scramble byte appears in any recording; greetings, auth switches and
    `admin` replies carry their placeholders; every recorded connection ends with the server closing
    it; every scenario records the probe check and the two distinct-value checks as passed; the new
    commands, the prepared CALL, the zero-date rows, the compressed non-repeating results, the input
    lines (and no unrecorded input), and the LOCAL INFILE exchanges (the client's packets recorded
    after the 0xFB packet, ending with an empty one) are in the recordings; the manifest carries the
    tools' sha256.
  - A client that deflates its frames at level 1 instead of the default records byte-identical
    wire_compressed and file_load_data scenarios (`compare`: identical).
  - Each of these gives a failed scenario (a `.partial`, `failed_cases`, exit 1): an unexpected ERR
    (the scenario goes on and the next one passes; `compare` then refuses the recording); a dropped
    connection; a server that stops answering (the response timeout); a server gone at the end; a
    DEFLATE file written as zlib instead of gzip; `CONNECTION_ID()` differing from the greeting; one
    scramble reused on every connection; a stray OK after the PLUGIN_AUTH_LENENC_CLIENT_DATA login;
    a prepared CALL whose EOF lacks PS_OUT_PARAMS. A changed status flag in one OK packet is reported by
    `compare` as a difference. A failed first start is a run error that stops the run. An existing
    `--file-dir` and a used record directory are refused; a wire-only run ignores `--file-dir`.
- known-answers.sql: all 41 comparisons in the file (9 CRC32, 9 short COMPRESS against zlib 1.2.13
  and Python's zlib, 2 UNCOMPRESS of known bytes, and the 4 long inputs' md5 and COMPRESS md5 against
  both) re-derived from the file itself (/tmp/wire-offline/ka_verify.py), and the table above measured
  with the same C program, with obd's libz 1.3.1, and with miniz_oxide 0.9.1 and zlib-rs 0.6.8 built
  offline from the local cargo cache (/tmp/wire-offline/ka_discriminate.py, deflate-rs/).

## What still needs a live check on the reference

After the judge run ends: two runs on the archived reference, then `compare`, then a caught mutation
(00b's second sign-off). The runs are sequential and use the same `--file-dir` (the default; it must
not exist before a run).

```
cd /Users/colin/seekdb-dev/migrate-to-rust
for run in a b; do
  python3 migration/judge/families/wire/wire_scenarios.py \
    --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
    --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
    --base-dir /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-$run/base \
    --record-dir /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-$run/rec \
    --port 3891 \
    --init-sql migration/judge/reduced-init/init.sql \
    --init-user-sql migration/judge/reduced-init/init_user.sql \
    --gzip /usr/bin/gzip --zstd /opt/homebrew/bin/zstd \
    > /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-$run.log 2>&1
done
python3 .github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-a/rec \
  --right /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-b/rec \
  --out /Users/colin/seekdb-dev/mysqltest-runs/00b/wire-compare.json
```

The first run must confirm:

- **Statements and expectations written from the source, not yet seen on the reference.** Every
  `-- check: ... expected ...: no` is either a statement the reference rejects (fix the step, or keep
  the ERR as the golden bytes and change the expectation) or an error number that differs from the one
  expected. Most likely to need that: the MAP, SPARSEVECTOR, ARRAY, INT[] and VECTOR columns and their
  literals, the latin1 and gbk columns, FLOAT(7,3) and DOUBLE(12,4), the zero-date rows, the expression
  SELECTs, `DO 1`, `START TRANSACTION READ ONLY`, `SET sql_mode = default`, the stored procedures sent
  as one COM_QUERY, the prepared `CALL p_out(?)` and its PS_OUT_PARAMS check, error numbers 1364, 1406,
  1235 and 1094, the COM_STATISTICS string, OUTFILE's FORMAT clause with `BUFFER_SIZE`, the LOAD DATA
  error cases, and LOCAL INFILE with COMPRESSION 'GZIP' and on the compressed connection. Any edit
  changes the recorder sha256, so both runs are made again after it.
- **The two recordings are identical.** A difference names a field this README did not predict to
  vary; it gets a placeholder only if the source shows it varies between runs of one binary.
- **The probe check holds:** `SELECT CONNECTION_ID()` equals the greeting's id on the reference (derived
  from the source, above).
- **Closes:** the server closes the connection after COM_QUIT on every connection, after login failures,
  the SSL refusal, the bad handshake responses and the failed COM_CHANGE_USER, each within 10 seconds,
  with nothing sent after the last response.
- **The wait** after `SET GLOBAL max_allowed_packet` (at most 120 s) and the time of the whole run
  (expected a few minutes; the type scenarios create 68 tables each).
- **`admin` logins and changes succeed,** which checks the scramble-derived reply end to end.
- **known-answers.sql through family 5:** a `0` in any `is_known_answer` or `is_known_input` column, a
  failed `round_trip`, or an error on the 960000-byte input (a group_concat or UNCOMPRESS size limit the
  source reading missed) is decided at the second sign-off; the printed values are what the Rust build
  must match either way.

## Mutations for the second sign-off

The family must catch at least one injected mutation (PLAN.md section 4, "00b's exit"). Pick them in
code the port rewrites, not in the sql-nio code PLAN.md section 3 keeps (it keeps sql-nio as a plain
crate and drops its two C-ABI files, row_encode.rs and response_api.rs; the C++ is ported):

- src/observer/mysql/obmp_packet_sender.cpp:811: set `OB_SERVER_STATUS_NO_BACKSLASH_ESCAPES` to 0.
  wire_ok_err_eof's OK after `SET sql_mode = 'NO_BACKSLASH_ESCAPES'` loses 0x0200; mysqltest cannot see
  status flags, so only this family catches it.
- src/query/protocol/ob_mysql_protocol_util.cpp:185: set `out.microseconds_` to 0. The binary DATETIME(6)
  and TIMESTAMP(3) values in wire_binary_types lose their fraction and go from length 11 to 7, while
  the text protocol is unchanged.
- src/observer/mysql/ob_query_driver.cpp:113: set `OB_SERVER_PS_OUT_PARAMS` to 0. The prepared
  `CALL p_out(?)` in wire_multi_results fails its check and its EOF bytes differ.
- rust/sql-nio/src/lib.rs:91: change `RESPONSE_BATCH_TARGET`, which response_api.rs uses to publish
  batches (:70, :134-137). The frame boundaries of wire_compressed's multi-batch results move; a plain
  connection shows nothing, since batches are invisible without compression.
- For the files: the gzip level at src/sql/engine/basic/ob_select_into_basic.cpp:258 (file_outfile's
  GZIP and DEFLATE bytes).

Mutations in kept code, such as `AUTH_PLUGIN_DATA_LEN` in rust/sql-nio/src/handshake.rs or the trailing
empty packet in `frame_layout` (rust/sql-nio/src/response.rs:900-911), show that the family sees wire
changes, but not that it guards the code the port rewrites.
