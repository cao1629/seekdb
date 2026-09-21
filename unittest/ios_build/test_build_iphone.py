"""Validate iOS build routing and failure propagation without compiling the engine."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class IPhoneBuildTests(unittest.TestCase):
    """Exercise the real script with isolated external-tool stand-ins."""

    def setUp(self):
        """Create a repository-local fixture with no access to production build outputs."""
        parent = ROOT / "build_ios_script_tests"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / "build.iphone.sh", self.root)
        (self.root / "rust").mkdir()
        (self.root / "rust/rust-toolchain.toml").write_text('channel = "1.98.1"\n')
        binaries = self.root / "bin"
        binaries.mkdir()
        stub = binaries / "stub"
        stub.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['CALL_LOG'], 'a') as out:
    out.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'xcrun': print('/sdk')
elif name == 'rustup':
    if 'list' in sys.argv: print('aarch64-apple-ios\\naarch64-apple-ios-sim')
    else: print('rustc 1.98.1')
elif name == 'cmake': sys.exit(int(os.environ.get('CMAKE_EXIT', '0')))
""")
        stub.chmod(0o755)
        for name in ("cmake", "cargo", "rustup", "xcrun"):
            (binaries / name).symlink_to(stub)
        self.log = self.root / "calls.jsonl"
        self.env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}",
                        CARGO=str(binaries / "cargo"), RUSTUP=str(binaries / "rustup"),
                        CALL_LOG=str(self.log), SEEKDB_IOS_MIN_FREE_GIB="0")

    def run_script(self, *args):
        """Run the copied script and return its status and captured diagnostics."""
        return subprocess.run(["/bin/bash", str(self.root / "build.iphone.sh"), *args],
                              env=self.env, capture_output=True, text=True)

    def calls(self):
        """Read the actual argument vectors observed by external-tool stand-ins."""
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_device_build(self):
        """Default compilation must select device SDK and the static engine target."""
        result = self.run_script("--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        self.assertIn("-DCMAKE_OSX_SYSROOT=iphoneos", calls[0])
        self.assertEqual(calls[1][-4:], ["--target", "oceanbase_static", "--parallel", "2"])

    def test_simulator_configuration_only(self):
        """Simulator configuration must not accidentally compile or select a device SDK."""
        result = self.run_script("--simulator", "--configure-only", "--", "-DTEST_VALUE=a b")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        self.assertEqual(len(calls), 1)
        self.assertIn("-DCMAKE_OSX_SYSROOT=iphonesimulator", calls[0])
        self.assertIn("-DTEST_VALUE=a b", calls[0])

    def test_configuration_failure_is_not_hidden_by_tee(self):
        """A failed configuration must stop before compilation and preserve its exit status."""
        self.env["CMAKE_EXIT"] = "23"
        result = self.run_script()
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(len([call for call in self.calls() if call[0] == "cmake"]), 1)

    def test_dependency_only_does_not_require_rust(self):
        """Route dependency builds to the local driver without touching the engine."""
        driver = self.root / "deps/ios-build/build.py"
        driver.parent.mkdir(parents=True)
        driver.write_text("import sys; print(repr(sys.argv[1:]))\n")
        self.env["CARGO"] = "/missing/cargo"
        self.env["RUSTUP"] = "/missing/rustup"
        result = self.run_script("--deps-only", "--simulator", "--jobs", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'--simulator'", result.stdout)
        self.assertIn("'--jobs', '3'", result.stdout)
        self.assertFalse(self.log.exists())

    def test_invalid_arguments_fail_before_tools(self):
        """Invalid job counts must fail without initializing or compiling anything."""
        result = self.run_script("--jobs", "0")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
