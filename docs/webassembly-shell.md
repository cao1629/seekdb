# seekdb WebAssembly shell

A browser SQL shell backed by the seekdb engine compiled to WebAssembly. The
terminal layout, command history, and example entry point take inspiration from
the [Lite4MariaDB shell](https://lite4mariadb.shyim.de/repl?run=example). Its
[JavaScript SDK and Worker API](https://github.com/shyim/lite4mariadb/tree/main/wasm/src)
also informed the loading, result, error, and close behavior. This shell uses
seekdb's existing JavaScript API and WebAssembly engine.

## Run an existing build

From this checkout, pass an engine build made from this branch (see
[Build from source](#build-from-source)):

```sh
python3 tools/wasm/serve-shell.py \
  --build-dir build_wasm_engine \
  --port 8767
```

Open [the shell](http://127.0.0.1:8767/shell.html) or
[run the Hybrid Search example](http://127.0.0.1:8767/shell.html?run=example).
The first load downloads the engine and initializes a database in the browser.
Keep the server running; press Ctrl+C in its terminal to stop it.

The launcher copies only `seekdb_wasm_database.mjs` and
`seekdb_wasm_database.wasm` into this checkout's ignored `build_wasm_shell/`
directory. It serves the shell and JavaScript API from this checkout, so reloading
the page picks up source edits. The server binds to `127.0.0.1` and serves an
explicit list of assets. It does not expose the repository or execute SQL.
Reusing another build is appropriate when its engine source and toolchain match;
rebuild after changing C++, Rust, or the runtime ABI. Builds from
`feature/webassembly` predate the storage argument and the WasmFS link, so they
do not start with this shell.

## Use the shell

The database starts automatically. Once it is ready, choose **Hybrid Search** or
**Fork Table** from **Examples** to run its SQL immediately, or enter SQL in
the prompt. Hybrid Search combines full-text matching, a category filter, and
vector distance. Fork Table creates a copy and shows that changes to either table
do not affect the other. Each example recreates its own demo tables in `playground`
so it can be run repeatedly.

| Input | Action |
| --- | --- |
| Enter | Run SQL ending in a semicolon |
| Ctrl / Command + Enter | Run SQL with or without a final semicolon |
| Shift + Enter | Add a new line |
| Up / Down | Browse history at the first or last input line |
| Escape / Ctrl + C | Cancel the active query |
| Ctrl + L | Clear output while keeping tables and history |
| `\clear` | Clear output |
| `\tables` | Show tables |
| `\databases` | Show databases |

Statements run in order and stop at the first error. Results display up to
500 rows and 100 columns as ASCII tables. Values and column names longer than
120 characters are shortened for display. Use `LIMIT` for large
queries. Statement splitting supports default MySQL quoting and
`NO_BACKSLASH_ESCAPES`. Use backticks for quoted identifiers; `ANSI_QUOTES`,
`DELIMITER`, and stored-program scripts are not supported.

Canceling disconnects the current session and opens a new one in the same
database. Committed data remains, uncommitted changes roll back, and session
settings reset. Closing the database stops the engine; whether its data
survives depends on the storage mode below.

### Storage

The shell starts with OPFS when the browser supports both OPFS and Web Locks,
unless a previous storage choice was saved. Otherwise it starts in memory.
Choose **Memory** or **OPFS** from **New Instance** to close the current engine,
discard its data, clear the terminal, and start an empty database in that mode.
Selecting the current mode also creates a new instance. Creating an OPFS instance
clears any previously stored database; leaving a running OPFS instance for Memory
also clears its stored data. If clearing fails, startup stops and shows the error.

| Item | Effect |
| --- | --- |
| Memory | The data directory lives in Wasm memory; it is gone when the database is closed or the page is reloaded |
| OPFS | The data directory lives in the origin private file system (OPFS); committed data survives reloads and browser restarts until New Instance clears it |

The choice is remembered in this browser and applies on the next load; the
badge next to the database name shows `memory://` or `opfs://`. Reloading the page
reopens an OPFS database without clearing it; **New Instance** starts empty.
One tab at a time may open the stored database: a second tab is told so and can
switch to memory, and a page outside this shell that still holds the files is
reported as locking them, to be closed before reloading the page. Browsers
without OPFS or Web Locks can use Memory only.

## Build from source

Use Emscripten 4.0.23 and the pinned Rust nightly. Host parser generation also
needs bison 2.4.1, flex, and the native dependency headers described in
[engine dependency setup](webassembly.md#configure-engine-dependencies).
After activating Emscripten and installing those dependencies:

```sh
rustup toolchain install nightly-2026-09-07 --profile minimal \
  --component rust-src --target wasm32-unknown-emscripten
bash tools/wasm/build-deps.sh
bash tools/wasm/build-rust-nio.sh
bash tools/wasm/build-engine.sh build_wasm_engine
cmake --build build_wasm_engine --target seekdb_wasm_database --parallel 3
python3 tools/wasm/serve-shell.py --build-dir build_wasm_engine
```

When the host tools or headers are outside this checkout's `deps/3rd`, pass
`-DSEEKDB_HOST_DEVTOOLS=/path/to/devtools` and
`-DSEEKDB_HEADER_DEPS=/path/to/devel/include` to `build-engine.sh`.

CMake copies the shell, API, and Worker assets beside the generated engine module.
That build directory can also be served with the existing server:

```sh
python3 tools/wasm/serve.py build_wasm_engine --port 8767
```

Open `/shell.html` when using this server. A static host must send
`Cross-Origin-Opener-Policy: same-origin` and
`Cross-Origin-Embedder-Policy: require-corp` to enable `SharedArrayBuffer` for
Emscripten pthreads. Use HTTPS or loopback HTTP, and serve `.wasm` as
`application/wasm` and `.mjs` as JavaScript. Opening the HTML as a file will not
work.

## Run tests

Use the Node runtime from the activated Emscripten SDK:

```sh
node --test unittest/wasm/shell-sql.test.mjs
node --test unittest/wasm/shell-format.test.mjs
node unittest/wasm/test_shell_examples.mjs build_wasm_engine/seekdb_wasm_database.mjs
```

The first two commands check SQL statement splitting, command history, and result
formatting. The third runs the examples and session checks against the real Wasm engine. Pass another
build's `seekdb_wasm_database.mjs` path as the final argument when reusing a build.
Node has no OPFS, so these run in memory mode; the persistent mode is covered by
the browser cases in `database-browser.html`, which add a stored reopen, the
one-instance rule and clearing when the browser offers OPFS. Those cases delete
the database stored in OPFS for their origin, so do not run them on the origin
of a shell whose stored data you want to keep.

## How SQL runs

```text
shell.html + shell.mjs
  → Database / Session API (database.mjs)
  → postMessage to database-worker.mjs
  → MySQL packets over the in-memory transport
  → seekdb C++ / Rust engine in WebAssembly
  → result events back to the shell
```

The main browser thread handles input and renders results. A dedicated Web Worker
owns the database runtime. Emscripten pthreads run engine work, while the existing
JavaScript API streams column, row, and completion events back to the shell.
The shell uses the real engine; startup failures are displayed as errors.

The engine runs on Emscripten WasmFS. `Database.open({storage: 'memory'})`, the
JavaScript API default, keeps the data directory in the WasmFS memory backend, so it is gone
when the database is closed or the page is reloaded. `Database.open({storage:
'opfs'})` mounts the origin private file system at the data directory instead:
the files live at the OPFS root of the page's origin, survive reloads, and are
opened by one instance per origin, guarded by a Web Lock. Committed data was
recovered after a clean close and after a reload without one in headless
Chrome; a durable commit contract on OPFS is not established yet.
`Database.clearPersistentStorage()` removes the stored files. No database
server, TCP connection, or SQL API service is required. The adapter that lets
the engine run on OPFS, and what it does not cover, is described in
[webassembly.md](webassembly.md#storage-modes-and-the-wasmfs-adapter).

The current engine uses a 512 MiB initial Wasm memory, can grow to 2 GiB, and
prewarms 64 pthread workers. These build settings make the shell most suitable
for a desktop browser. The engine binary is large, and startup can take several
seconds on the first load.
