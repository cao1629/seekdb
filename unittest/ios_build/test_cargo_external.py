"""Exercise generated Cargo commands with real CMake and a recording executable."""
import json
import os
from pathlib import Path
import subprocess
import sys
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

    def _record_jemalloc_environment(
            self, *, system_name="Darwin", macos27=False,
            architecture="arm64"):
        """Configure a real Cargo custom command and return its captured env."""
        parent = ROOT / "build_ios_script_tests"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        fixture = Path(temporary.name)
        (fixture / "rust").mkdir()
        (fixture / "rust/rust-toolchain.toml").write_text(
            'channel = "1.98.1"\n', encoding="utf-8")
        (fixture / "Cargo.toml").write_text(
            '[package]\nname="fixture"\nversion="0.0.0"\n',
            encoding="utf-8")
        (fixture / "Cargo.lock").write_text("", encoding="utf-8")
        record = fixture / "record.json"
        cargo = fixture / "cargo"
        cargo.write_text(f'''#!{sys.executable}
import json, os
from pathlib import Path
root = Path(os.environ['JEMALLOC_SYS_OUTPUT_DIR'])
(root / 'lib').mkdir(parents=True, exist_ok=True)
(root / 'include/jemalloc').mkdir(parents=True, exist_ok=True)
(root / 'lib/libjemalloc_pic.a').touch()
(root / 'include/jemalloc/jemalloc.h').touch()
Path(os.environ['RECORD']).write_text(json.dumps({{
    'configure': os.environ['JEMALLOC_SYS_CONFIGURE_ARGS'],
    'developer': os.environ.get('DEVELOPER_DIR'),
}}))
''', encoding="utf-8")
        cargo.chmod(0o755)
        apple = system_name in {"Darwin", "iOS"}
        (fixture / "CMakeLists.txt").write_text(f'''
cmake_minimum_required(VERSION 3.22)
project(jemalloc_env C)
set(CARGO "{cargo}")
set(CMAKE_SYSTEM_NAME "{system_name}")
set(APPLE {"TRUE" if apple else "FALSE"})
set(OB_MACOS27 {"TRUE" if macos27 else "FALSE"})
set(OB_MACOS_DEVELOPER_DIR "/developer")
set(CMAKE_OSX_ARCHITECTURES "{architecture}")
set(ARCHITECTURE "{architecture}")
set(SEEKDB_IOS_CLANG_TARGET "arm64-apple-ios18.0")
set(SEEKDB_IOS_SDK_PATH "/iphoneos.sdk")
set(SEEKDB_IOS_RUST_TARGET "aarch64-apple-ios")
set(CMAKE_OSX_DEPLOYMENT_TARGET "18.0")
set(ENV{{RECORD}} "{record}")
include("{ROOT}/deps/external/cmake/CargoExternal.cmake")
include("{ROOT}/deps/external/cmake/Jemalloc.cmake")
''', encoding="utf-8")
        build = fixture / "build"
        environment = dict(os.environ, RECORD=str(record))
        configured = subprocess.run(
            ["cmake", "-S", str(fixture), "-B", str(build)],
            env=environment, capture_output=True, text=True)
        self.assertEqual(
            0, configured.returncode, configured.stdout + configured.stderr)
        built = subprocess.run(
            ["cmake", "--build", str(build),
             "--target", "seekdb_jemalloc_build"],
            env=environment, capture_output=True, text=True)
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        return json.loads(record.read_text(encoding="utf-8"))

    def test_macos27_arm64_jemalloc_uses_explicit_darwin_host(self):
        """Switch to cross mode after the initial macOS 27 runtime probe fails."""
        environment = self._record_jemalloc_environment(macos27=True)

        self.assertIn("--host=aarch64-apple-darwin",
                      environment["configure"].splitlines())
        self.assertEqual("/developer", environment["developer"])

    def test_jemalloc_host_override_is_limited_to_macos27_arm64(self):
        """Preserve ordinary macOS, iOS, and Android configure contracts."""
        regular = self._record_jemalloc_environment(macos27=False)
        macos27_x86 = self._record_jemalloc_environment(
            macos27=True, architecture="x86_64")
        ios = self._record_jemalloc_environment(
            system_name="iOS", macos27=False)
        android = self._record_jemalloc_environment(
            system_name="Android", macos27=False)

        self.assertNotIn("--host=aarch64-apple-darwin",
                         regular["configure"].splitlines())
        self.assertNotIn("--host=aarch64-apple-darwin",
                         macos27_x86["configure"].splitlines())
        self.assertIn("--host=aarch64-apple-ios",
                      ios["configure"].splitlines())
        self.assertNotIn("--host=aarch64-apple-darwin",
                         ios["configure"].splitlines())
        self.assertNotIn("--host=aarch64-apple-darwin",
                         android["configure"].splitlines())


if __name__ == "__main__":
    unittest.main()
