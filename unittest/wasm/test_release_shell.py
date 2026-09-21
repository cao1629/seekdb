#!/usr/bin/env python3
# Copyright (c) 2025 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gzip
import http.client
import json
import runpy
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE = runpy.run_path(str(ROOT / "tools/wasm/release-shell.py"))


class QuietHandler(RELEASE["ReleaseHandler"]):
    def log_message(self, format, *args):
        pass


class ReleaseShellTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.build = self.root / "build"
        self.build.mkdir()
        for name in RELEASE["ASSETS"]:
            (self.build / name).write_bytes((name + "\n").encode() * 20)
        (self.build / "shell.html").write_text('<html><head><script type="module" src="./shell.mjs"></script></head></html>')
        (self.build / "seekdb_wasm_database.wasm").write_bytes(b"\0asm\x01\0\0\0")
        self.output = self.root / "release"
        self.manifest = RELEASE["package"](self.build, self.output)
        self.prefix = "/releases/" + self.manifest["build_id"]

    def start_server(self, output=None):
        output = output or self.output
        handler = partial(QuietHandler, root=output, assets=RELEASE["load_assets"](output))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        self.port = server.server_port

    def request(self, path, headers=None, method="GET"):
        with closing(http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)) as connection:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_package_is_complete_reproducible_and_versioned(self):
        second = self.root / "second"
        self.assertEqual(self.manifest, RELEASE["package"](self.build, second))
        self.assertEqual(set(RELEASE["ASSETS"]), set(self.manifest["input_sha256"]))
        html = (self.output / "shell.html").read_text()
        self.assertIn(f'<base href=".{self.prefix}/">', html)
        self.assertEqual((self.output / "index.html").read_text(), html)
        (self.build / "shell.mjs").write_text("export const changed = true;")
        changed = RELEASE["package"](self.build, self.root / "changed")
        self.assertNotEqual(self.manifest["build_id"], changed["build_id"])
        self.assertEqual(self.manifest["engine_sha256"], changed["engine_sha256"])

    def test_existing_output_and_missing_input_are_rejected(self):
        original = (self.output / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            RELEASE["package"](self.build, self.output)
        self.assertEqual((self.output / "manifest.json").read_bytes(), original)
        (self.build / "engine-version.mjs").unlink()
        with self.assertRaises(FileNotFoundError):
            RELEASE["package"](self.build, self.root / "missing")
        self.assertFalse((self.root / "missing").exists())

    def test_startup_rejects_changed_assets_and_escape_paths(self):
        path = self.output / self.manifest["assets"][self.prefix + "/shell.mjs"]["representations"]["identity"]["path"]
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            RELEASE["load_assets"](self.output)
        self.manifest["assets"][self.prefix + "/shell.mjs"]["representations"]["identity"]["path"] = "../build/shell.mjs"
        (self.output / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "does not match"):
            RELEASE["load_assets"](self.output)

    def test_html_updates_and_assets_use_immutable_compression(self):
        self.start_server()
        status, headers, body = self.request("/shell.html?run=example")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(self.prefix.encode(), body)
        status, headers, body = self.request(self.prefix + "/seekdb_wasm_database.wasm", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], RELEASE["IMMUTABLE"])
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(headers["Content-Type"], "application/wasm")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(headers["Cross-Origin-Embedder-Policy"], "require-corp")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(gzip.decompress(body), (self.build / "seekdb_wasm_database.wasm").read_bytes())
        _, headers, _ = self.request(self.prefix + "/shell.mjs")
        self.assertEqual(headers["Content-Type"], "text/javascript")

    def test_etag_is_specific_to_encoding_and_head_has_no_body(self):
        self.start_server()
        path = self.prefix + "/shell.mjs"
        _, compressed, _ = self.request(path, {"Accept-Encoding": "gzip"})
        status, identity, _ = self.request(path, {"If-None-Match": compressed["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotEqual(identity["ETag"], compressed["ETag"])
        status, headers, body = self.request(path, {"Accept-Encoding": "gzip", "If-None-Match": "W/" + compressed["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Cache-Control"], RELEASE["IMMUTABLE"])
        status, headers, body = self.request(path, {"Accept-Encoding": "gzip"}, method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], compressed["Content-Length"])
        self.assertEqual(body, b"")

    def test_unknown_paths_and_unacceptable_encodings_are_rejected(self):
        self.start_server()
        for path in ("/manifest.json", "/.clangd", "/%2e%2e/build/shell.mjs", self.prefix + "/"):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 404)
            self.assertEqual(headers["Cache-Control"], "no-store")
        status, _, _ = self.request(self.prefix + "/shell.mjs", {"Accept-Encoding": "*;q=0"})
        self.assertEqual(status, 406)
        _, headers, _ = self.request(self.prefix + "/shell.mjs", {"Accept-Encoding": "gzip;q=0"})
        self.assertNotIn("Content-Encoding", headers)

    @unittest.skipUnless(shutil.which("brotli"), "brotli executable is optional")
    def test_optional_brotli_and_weighted_negotiation(self):
        output = self.root / "brotli"
        RELEASE["package"](self.build, output, brotli=True)
        self.start_server(output)
        path = self.prefix + "/shell.mjs"
        status, headers, body = self.request(path, {"Accept-Encoding": "gzip, br"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "br")
        decoded = subprocess.run([shutil.which("brotli"), "-d", "-c"], input=body, stdout=subprocess.PIPE, check=True).stdout
        self.assertEqual(decoded, (self.build / "shell.mjs").read_bytes())
        _, headers, _ = self.request(path, {"Accept-Encoding": "gzip;q=1, br;q=0.5, identity;q=0"})
        self.assertEqual(headers["Content-Encoding"], "gzip")


if __name__ == "__main__":
    unittest.main()
