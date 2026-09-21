# QuickLang seekdb SQL compatibility probe

This suite validates the seekdb dialect needed for a future iOS backend switch.
QuickLang iOS currently uses SQLite; passing this suite does not change its backend.
All fixtures are synthetic and isolated in `ql_ios_probe`. Each run deletes fixture
rows in that database, never QuickLang's real database. Do not store user data there.

## Source and scope

`schema_source.json` records the QuickLang revision and SHA-256 of its ten canonical
`src/schema/*.sql` files. `schema_snapshot.h` preserves their types, collations,
indexes and constraints; only database qualification is added. Refresh explicitly:

```sh
python3 unittest/ios_build/quicklang/snapshot_schema.py /path/to/quicklang
```

The reviewed source is `src/crates/storage-seekdb/src` in QuickLang. Its current
working tree can contain uncommitted SQLite work; schema hashes identify the actual
DDL snapshot independently of the revision. These fixtures exercise representative
statement shapes and assertions, not every dynamically generated SQL value.

| Source | Covered behavior |
| --- | --- |
| `schema.rs`, `lib.rs` | Ten exact schemas, repeated CREATE, information_schema table inventory |
| `app_storage.rs` | State upsert, binary case-sensitive keys, UTF-8 hex literals with quote/backslash/NUL/Chinese, atomic rollback |
| `user_settings.rs` | Composite user/key JSON upsert, user isolation, scoped delete |
| `ai_profiles.rs` | INSERT IGNORE singleton initialization, JSON update, repeat initialization preserves data |
| `app_storage.rs` interpretation operations | Session upsert and user/time ordering; chunk composite keys, sequence ordering, lookup/upsert; owner check and transactional parent/child deletion |
| `listening.rs`, `speech_preferences.rs` | MEDIUMBLOB hex round-trip, JSON, locked version read, metadata update, preference upsert increments version without replacing audio, scoped delete |
| `lib.rs` review repository | INSERT IGNORE, event lookup/uniqueness, SELECT FOR UPDATE, event/state commit and rollback, stale-version update affects zero rows |
| `word_library.rs`, `dialect.rs` | Batch word insert, IN filter, case-sensitive keyset pagination, defaults, native VARCHAR array import/readback, empty array, NULL/JSON-array data and CHECK rejection |

JSON projections sometimes use JSON_EXTRACT/JSON_UNQUOTE to compare values without
depending on JSON whitespace. All result rows are checked in order and extra rows
are rejected. Expected-error cases require the exact engine error, not any failure.
The transaction runner pins one `ObMySQLTransaction` connection; it never emulates
transactions with independent pooled BEGIN/COMMIT statements. First failure stops
the suite; outstanding transactions roll back.

## Run and evidence

The existing UIKit SeekDB Probe automatically runs the suite after its basic SQL
counter test. Build using `build.iphone.sh` and `deps/ios-build/build_app.py` as
described in `docs/developer-guide/zh/ios-build.md`.

`Documents/quicklang-sql-results.jsonl` is truncated for each run and flushed after
each step. A final `complete:true,result:0` plus `quicklang_verified:true` in
`Documents/probe-status.json` establishes suite success. A partial file or nonzero
result is not success. The status file also records the current timestamp.

Use `SEEKDB_PROBE_AUTO_STOP=1` to exercise engine shutdown after SQL; omit it to
inspect the foreground running instance. Shutdown is a separate acceptance gate.

Still outside this suite: Rust/C ABI or MySQL socket integration, QuickLang UI,
concurrent writer races, full-size audio/word datasets, performance, clean-stop
persistence, migrations between SQLite and seekdb, vector search and model features.
The basic lifecycle counter separately tests committed data recovery across launches.

## Backend switching boundary

The concurrent QuickLang iOS implementation currently owns application-side changes:
shared repositories call `native.execute` / `native.transaction`, while `dialect.rs`
contains backend-specific upserts, INSERT IGNORE, FOR UPDATE and array projection.
SQLite is selected for iOS; desktop seekdb remains the default. The existing adapter
name is retained for caller compatibility. This suite does not modify that checkout.

A future seekdb iOS driver must implement the same row contract (nullable UTF-8
cells, explicit HEX for blobs), connection ownership, transaction rollback on error,
and structured application errors. Its dialect and canonical schemas must be selected
together with the driver. Backend identity should be explicit at the application
factory; unsupported selections must fail rather than silently opening another engine.
SQLite and seekdb must have separate data directories and version markers. Switching
existing users requires logical export/import and validation; their files cannot be
opened interchangeably. Do not expose a production switch until seekdb clean shutdown,
resource limits and client integration pass. A compile-time backend selection today
does not imply a runtime hot-switch or an implemented seekdb iOS Rust driver.
