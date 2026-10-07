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
    if 'toolchain' in sys.argv and 'list' in sys.argv: print('' if os.environ.get('RUST_MISSING') else '1.98.1-aarch64-apple-darwin')
    elif 'list' in sys.argv: print('aarch64-apple-ios\\naarch64-apple-ios-sim')
    else: print('rustc 1.98.1')
elif name == 'cmake': sys.exit(int(os.environ.get('CMAKE_EXIT', '0')))
""")
        stub.chmod(0o755)
        for name in ("cmake", "cargo", "rustup", "xcrun"):
            (binaries / name).symlink_to(stub)
        self.headers = self.root / "deps/3rd/usr/local/oceanbase/deps/devel"
        for header in ("grpcpp/grpcpp.h", "rapidjson/error/en.h", "boost/version.hpp"):
            path = self.headers / "include" / header
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        tools = self.root / "deps/3rd/usr/local/oceanbase/devtools/bin"
        tools.mkdir(parents=True)
        for name in ("bison", "flex"):
            path = tools / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        driver = self.root / "deps/ios-build/build.py"
        driver.parent.mkdir(parents=True)
        driver.write_text("import json, os, sys\n"
                          "with open(os.environ['CALL_LOG'], 'a') as out: "
                          "out.write(json.dumps(['dependencies', *sys.argv[1:]]) + '\\n')\n"
                          "sys.exit(int(os.environ.get('DEPS_EXIT', '0')))\n")
        initializer = self.root / "build.sh"
        initializer.write_text("#!/bin/sh\n"
                               "printf '%s\\n' '[\"host-init\",\"init\"]' >> \"$CALL_LOG\"\n")
        initializer.chmod(0o755)
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
        """Default compilation must select device SDK, Debug profile, and final link validation."""
        result = self.run_script("--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        self.assertIn("-DCMAKE_OSX_SYSROOT=iphoneos", calls[0])
        self.assertEqual(calls[1][-4:], ["--target", "seekdb_ios_link_check", "--parallel", "2"])

    def test_missing_rust_toolchain_installed_automatically(self):
        """A fresh local Rust cache must not require the legacy init flag."""
        self.env["RUST_MISSING"] = "1"
        result = self.run_script("--configure-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[:3] == ["rustup", "toolchain", "install"] for call in self.calls()))

    def test_explicit_dependency_prefix_is_not_modified(self):
        """An explicitly managed dependency directory must bypass bootstrap builds."""
        result = self.run_script("--deps-prefix", str(self.root / "deps/ios/iphoneos/devel"), "--configure-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] == "dependencies" for call in self.calls()))

    def test_release_profile(self):
        """Release must retain the supported optimized CMake configuration."""
        result = self.run_script("release", "--configure-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        configure = next(call for call in self.calls() if call[0] == "cmake")
        self.assertIn("-DCMAKE_BUILD_TYPE=RelWithDebInfo", configure)

    def test_framework_target_uses_socket_only_production_configuration(self):
        """Package a hookless framework without unsupported standby gRPC dependencies."""
        wrapper = self.root / "unittest/ios_build/rustc_lldb_wrapper.py"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
        self.env.pop("RUSTC_WRAPPER", None)
        result = self.run_script("release", "--target", "seekdb_ios_framework")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        for flag in ("-DSEEKDB_IOS_FRAMEWORK=ON", "-DSEEKDB_IOS_TEST_HOOKS=OFF", "-DOB_ENABLE_STANDBY=OFF"):
            self.assertIn(flag, calls[0])
        self.assertIn("seekdb_ios_framework", calls[1])

    def test_default_prepares_dependencies(self):
        """No-option builds must prepare reusable target dependencies before CMake."""
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        dependencies = next(call for call in calls if call[0] == "dependencies")
        self.assertIn("--reuse", dependencies)
        configure = next(call for call in calls if call[0] == "cmake")
        self.assertIn("-DCMAKE_BUILD_TYPE=Debug", configure)
        self.assertLess(calls.index(dependencies), calls.index(configure))

    def test_missing_host_headers_trigger_initialization(self):
        """A fresh checkout must bootstrap host headers with the supported init command."""
        shutil.rmtree(self.headers)
        result = self.run_script()
        self.assertIn(["host-init", "init"], self.calls())
        self.assertIn("missing dependency headers", result.stderr)

    def test_dependency_failure_stops_engine_configuration(self):
        """A failed dependency build must propagate its status before engine work."""
        self.env["DEPS_EXIT"] = "19"
        result = self.run_script()
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertFalse(any(call[0] == "cmake" for call in self.calls()))

    def test_simulator_configuration_only(self):
        """Simulator configuration must not accidentally compile or select a device SDK."""
        result = self.run_script("--simulator", "--configure-only", "--", "-DTEST_VALUE=a b")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        self.assertEqual(len(calls), 1)
        self.assertIn("-DCMAKE_OSX_SYSROOT=iphonesimulator", calls[0])
        self.assertIn("-DTEST_VALUE=a b", calls[0])

    def test_explicit_build_directory_is_used_for_isolated_artifacts(self):
        """A production isolation build must not overwrite test-hook outputs."""
        isolated = self.root / "build_ios_production"
        result = self.run_script(
            "--build-dir", str(isolated), "--target",
            "seekdb_ios_link_check")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call for call in self.calls() if call[0] == "cmake"]
        self.assertEqual(str(isolated), calls[0][calls[0].index("-B") + 1])
        self.assertEqual(str(isolated), calls[1][2])

    def test_configuration_failure_is_not_hidden_by_tee(self):
        """A failed configuration must stop before compilation and preserve its exit status."""
        self.env["CMAKE_EXIT"] = "23"
        result = self.run_script()
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(len([call for call in self.calls() if call[0] == "cmake"]), 1)

    def test_dependency_only_does_not_require_rust(self):
        """Route dependency builds to the local driver without touching the engine."""
        driver = self.root / "deps/ios-build/build.py"
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_text("import sys; print(repr(sys.argv[1:]))\n")
        self.env["CARGO"] = "/missing/cargo"
        self.env["RUSTUP"] = "/missing/rustup"
        result = self.run_script("--deps-only", "--simulator", "--jobs", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'--simulator'", result.stdout)
        self.assertIn("'--jobs', '3'", result.stdout)
        self.assertFalse(self.log.exists())

    def test_default_uses_existing_public_headers(self):
        """Missing iOS headers must use host headers while retaining iOS libraries."""
        result = self.run_script("--configure-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = next(call for call in self.calls() if call[0] == "cmake")
        self.assertIn(f"-DSEEKDB_IOS_HEADER_PREFIX={self.headers}", call)
        self.assertIn(f"-DDEP_DIR={self.root}/deps/ios/iphoneos/devel", call)

    def test_complete_ios_headers_take_precedence(self):
        """A complete target prefix must avoid the host-header fallback."""
        target = self.root / "deps/ios/iphoneos/devel"
        shutil.copytree(self.headers, target)
        result = self.run_script("--configure-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = next(call for call in self.calls() if call[0] == "cmake")
        self.assertIn(f"-DSEEKDB_IOS_HEADER_PREFIX={target}", call)

    def test_missing_explicit_headers_fail_before_configuration(self):
        """An invalid explicit prefix must fail clearly instead of silently falling back."""
        result = self.run_script("--headers-prefix", str(self.root / "missing"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing dependency headers", result.stderr)
        self.assertFalse(any(call[0] == "cmake" for call in self.calls()))

    def test_invalid_arguments_fail_before_tools(self):
        """Invalid job counts must fail without initializing or compiling anything."""
        result = self.run_script("--jobs", "0")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
