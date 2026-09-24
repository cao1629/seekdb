#!/usr/bin/env python3
"""Contract tests for the tracked macOS Cargo build-script wrapper."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
WRAPPER_PATH = SCRIPT_DIR / "rustc_lldb_wrapper.py"


def _load_wrapper_module():
    """Load the wrapper as a module without invoking its command-line entry."""
    spec = importlib.util.spec_from_file_location(
        "rustc_lldb_wrapper", WRAPPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _macho_executable_bytes() -> bytes:
    """Return a minimal 64-bit host Mach-O executable header."""
    build_version = struct.pack("<IIIIII", 0x32, 24, 1, 0, 0, 0)
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", 0x0100000C, 0, 2, 1, len(build_version), 0, 0)
    return header + build_version


class RustcLldbWrapperTest(unittest.TestCase):
    """Require bounded replacement and exact exit propagation."""

    def _write_compiler(self, root: Path, body: str) -> Path:
        """Create one executable fake rustc implementation."""
        compiler = root / "fake rustc.py"
        compiler.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n" + body,
            encoding="utf-8")
        compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
        return compiler

    def test_compiles_first_then_wraps_only_new_strict_macho_output(self):
        """Preserve old files and wrap one newly emitted host build script."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "output with ' quote"
            output.mkdir()
            old = output / "build_script_build-aaaa"
            old.write_bytes(_macho_executable_bytes())
            log = root / "rustc argv.json"
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                f"pathlib.Path({str(log)!r}).write_text(json.dumps(args))\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                f"target.write_bytes({_macho_executable_bytes()!r})\n"
                "target.chmod(0o755)\n"))

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "--out-dir", str(output),
            ], check=False, capture_output=True, text=True)

            launcher = output / "build_script_build-deadbeef"
            real = output / "build_script_build-deadbeef.real"
            compiler_arguments = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                ["--crate-name", "build_script_build", "--out-dir", str(output)],
                compiler_arguments)
            self.assertTrue(real.is_file())
            self.assertEqual(_macho_executable_bytes(), real.read_bytes())
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertIn(
                "--run-build-script", launcher.read_text(encoding="utf-8"))
            self.assertEqual(0, subprocess.run(
                ["/bin/sh", "-n", str(launcher)], check=False).returncode)
            self.assertFalse((old.with_suffix(".real")).exists())

    def test_compile_failure_is_returned_without_replacement(self):
        """Return the real compiler status and leave no launcher behind."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            compiler = self._write_compiler(root, "sys.exit(17)\n")

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "--out-dir", str(output),
            ], check=False)
            self.assertEqual(17, result.returncode)
            self.assertEqual([], list(output.iterdir()))

    def test_compile_signal_is_returned_as_the_shell_status(self):
        """Preserve the conventional 128-plus-signal compiler result."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            compiler = self._write_compiler(
                root, "import signal\nos.kill(os.getpid(), signal.SIGTERM)\n")

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "--out-dir", str(output),
            ], check=False)

        self.assertEqual(143, result.returncode)

    def test_non_macho_output_is_left_unchanged(self):
        """Do not replace a strict-name output that is not a host Mach-O."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                "target.write_bytes(b'not-mach-o')\n"
                "target.chmod(0o755)\n"))

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "--out-dir", str(output),
            ], check=False)

            target = output / "build_script_build-deadbeef"
            self.assertEqual(0, result.returncode)
            self.assertEqual(b"not-mach-o", target.read_bytes())
            self.assertFalse(target.with_name(f"{target.name}.real").exists())

    def test_existing_real_file_blocks_replacement(self):
        """Refuse to overwrite preserved state from an earlier failed attempt."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            preserved = output / "build_script_build-deadbeef.real"
            preserved.write_bytes(b"preserved")
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                f"target.write_bytes({_macho_executable_bytes()!r})\n"
                "target.chmod(0o755)\n"))

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "--out-dir", str(output),
            ], check=False)

            self.assertEqual(125, result.returncode)
            self.assertEqual(b"preserved", preserved.read_bytes())

    def test_launcher_propagates_fake_build_script_exit_and_arguments(self):
        """Return the LLDB-launched build-script status without shell splitting."""
        wrapper = _load_wrapper_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log = root / "arguments.json"
            build_script = root / "build_script_build-deadbeef.real"
            build_script.write_bytes(_macho_executable_bytes())
            build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)
            fake_xcrun = root / "fake xcrun.py"
            fake_xcrun.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "separator = sys.argv.index('--')\n"
                f"pathlib.Path({str(log)!r}).write_text("
                "json.dumps(sys.argv[separator + 1:]))\n"
                "raise SystemExit(23)\n",
                encoding="utf-8")
            fake_xcrun.chmod(fake_xcrun.stat().st_mode | stat.S_IXUSR)

            status = wrapper.run_build_script(
                build_script, ("argument with spaces", "literal'quote"),
                xcrun=fake_xcrun)
            arguments = json.loads(log.read_text(encoding="utf-8"))

        self.assertEqual(23, status)
        self.assertEqual(
            [str(build_script.resolve()),
             "argument with spaces", "literal'quote"],
            arguments)

    def test_symlink_and_noncanonical_output_directory_are_rejected(self):
        """Never replace a symlink or an output reached through dot segments."""
        for anomaly in ("symlink", "relative"):
            with self.subTest(anomaly=anomaly), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                output = root / "out"
                output.mkdir()
                compiler = self._write_compiler(root, (
                    "args = sys.argv[1:]\n"
                    "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                    "out.mkdir(parents=True, exist_ok=True)\n"
                    "target = out / 'build_script_build-deadbeef'\n"
                    + ("target.symlink_to('/dev/null')\n" if anomaly == "symlink"
                       else f"target.write_bytes({_macho_executable_bytes()!r})\n")
                ))
                output_argument = (
                    str(output) if anomaly == "symlink" else "out/../out")

                result = subprocess.run([
                    sys.executable, str(WRAPPER_PATH), str(compiler),
                    "--crate-name", "build_script_build",
                    "--out-dir", output_argument,
                ], cwd=root, check=False, capture_output=True, text=True)

            self.assertNotEqual(0, result.returncode)
            self.assertFalse(
                (output / "build_script_build-deadbeef.real").exists())


if __name__ == "__main__":
    unittest.main()
