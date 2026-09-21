"""Exercise generated Cargo commands with real CMake and a recording executable."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CargoExternalTests(unittest.TestCase):
    """Verify cross-build arguments survive CMake generation and execution."""

    def test_environment_and_failure_propagation(self):
        """Preserve multiline values, select explicit Cargo, and reject build failure."""
        parent = ROOT / "build_ios_script_tests"
        parent.mkdir(exist_ok=True)
        for exit_code in (0, 23):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory(dir=parent) as temporary:
                fixture = Path(temporary)
                (fixture / "rust").mkdir()
                (fixture / "rust/rust-toolchain.toml").write_text('channel = "1.98.1"\n')
                (fixture / "Cargo.toml").write_text("")
                (fixture / "Cargo.lock").write_text("")
                cargo = fixture / "cargo"
                cargo.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['RECORD']).write_text(json.dumps({
    'args': sys.argv[1:], 'options': os.environ['CONFIGURE_OPTIONS']}))
if int(os.environ['BUILD_EXIT']) == 0:
    Path(os.environ['ARTIFACT']).touch()
sys.exit(int(os.environ['BUILD_EXIT']))
''')
                cargo.chmod(0o755)
                (fixture / "CMakeLists.txt").write_text(f'''
cmake_minimum_required(VERSION 3.22)
project(cargo_runner NONE)
set(CARGO "{cargo}")
set(CMAKE_SYSTEM_NAME iOS)
set(SEEKDB_IOS_RUST_TARGET aarch64-apple-ios)
include("{ROOT}/deps/external/cmake/CargoExternal.cmake")
seekdb_external_add_cargo_artifacts(
  NAME probe MANIFEST "${{CMAKE_SOURCE_DIR}}/Cargo.toml"
  OUTPUT_ROOT "${{CMAKE_BINARY_DIR}}/artifacts"
  OUTPUTS "${{CMAKE_BINARY_DIR}}/artifact.a"
  ENV "CONFIGURE_OPTIONS=first option\\nsecond option"
      "RECORD=${{CMAKE_BINARY_DIR}}/record.json"
      "ARTIFACT=${{CMAKE_BINARY_DIR}}/artifact.a"
      "BUILD_EXIT={exit_code}"
  COMMENT "Testing Cargo command")
''')
                build = fixture / "build"
                configured = subprocess.run(
                    ["cmake", "-S", str(fixture), "-B", str(build)],
                    capture_output=True, text=True)
                self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
                result = subprocess.run(
                    ["cmake", "--build", str(build), "--target", "probe_build"],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, exit_code == 0,
                                 result.stdout + result.stderr)
                record = json.loads((build / "record.json").read_text())
                self.assertEqual(record['options'], 'first option\nsecond option')
                self.assertIn('aarch64-apple-ios', record['args'])
                self.assertEqual((build / "artifact.a").exists(), exit_code == 0)


if __name__ == "__main__":
    unittest.main()
