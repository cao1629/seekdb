#!/usr/bin/env python3
"""Contract tests for the tracked macOS Cargo build-script wrapper."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
        """Wrap a refreshed healthy unit and retain its prior real binary."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "output with ' quote"
            output.mkdir()
            old = output / "build_script_build-aaaa"
            old.write_bytes(_macho_executable_bytes())
            log = root / "rustc argv.json"
            generation = root / "generation"
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                f"pathlib.Path({str(log)!r}).write_text(json.dumps(args))\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                f"generation = pathlib.Path({str(generation)!r})\n"
                "number = int(generation.read_text()) + 1 "
                "if generation.exists() else 1\n"
                "generation.write_text(str(number))\n"
                f"target.write_bytes({_macho_executable_bytes()!r} + "
                "str(number).encode())\n"
                "target.chmod(0o755)\n"))

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "-C", "extra-filename=-deadbeef",
                "--out-dir", str(output),
            ], check=False, capture_output=True, text=True)

            launcher = output / "build_script_build-deadbeef"
            real = output / "build_script_build-deadbeef.real"
            compiler_arguments = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                ["--crate-name", "build_script_build", "--out-dir", str(output)],
                [argument for argument in compiler_arguments
                 if argument not in ("-C", "extra-filename=-deadbeef")])
            self.assertTrue(real.is_file())
            self.assertEqual(_macho_executable_bytes() + b"1", real.read_bytes())
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertIn(
                "--run-build-script", launcher.read_text(encoding="utf-8"))
            self.assertEqual(0, subprocess.run(
                ["/bin/sh", "-n", str(launcher)], check=False).returncode)
            self.assertFalse((old.with_suffix(".real")).exists())

            second = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "-C", "extra-filename=-deadbeef",
                "--out-dir", str(output),
            ], check=False, capture_output=True, text=True)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertIn(
                "--run-build-script", launcher.read_text(encoding="utf-8"))
            self.assertEqual(_macho_executable_bytes() + b"2", real.read_bytes())
            backups = list(output.glob(
                ".build_script_build-deadbeef.previous-*.real"))
            self.assertEqual(1, len(backups))
            self.assertEqual(
                _macho_executable_bytes() + b"1", backups[0].read_bytes())

    def test_raw_output_with_existing_real_remains_fail_closed(self):
        """Reject stale raw-plus-real state before invoking the compiler."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            raw = output / "build_script_build-deadbeef"
            raw.write_bytes(_macho_executable_bytes())
            raw.chmod(0o755)
            preserved = raw.with_name(f"{raw.name}.real")
            preserved.write_bytes(_macho_executable_bytes() + b"old")
            preserved.chmod(0o755)
            called = root / "called"
            compiler = self._write_compiler(
                root, f"pathlib.Path({str(called)!r}).touch()\n")

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "-C", "extra-filename=-deadbeef",
                "--out-dir", str(output),
            ], check=False)

        self.assertEqual(125, result.returncode)
        self.assertFalse(called.exists())

    def test_interrupted_healthy_rewrap_recovers_without_losing_old_real(self):
        """Resume a partial rename transaction and retain the old executable."""
        wrapper = _load_wrapper_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            launcher = output / "build_script_build-deadbeef"
            real = launcher.with_name(f"{launcher.name}.real")
            old_bytes = _macho_executable_bytes() + b"old"
            new_bytes = _macho_executable_bytes() + b"new"
            real.write_bytes(old_bytes)
            real.chmod(0o755)
            launcher.write_text(
                wrapper._launcher_source(real), encoding="utf-8")
            launcher.chmod(0o755)
            prestate = wrapper._healthy_pair_state(launcher)
            wrapper._prepare_healthy_pair(launcher, prestate)
            launcher.write_bytes(new_bytes)
            launcher.chmod(0o755)
            original_replace = os.replace
            replace_count = 0

            def interrupt_third_replace(source, destination):
                """Crash after manifest commit and old-real quarantine."""
                nonlocal replace_count
                replace_count += 1
                if replace_count == 3:
                    raise OSError("simulated interruption")
                return original_replace(source, destination)

            with mock.patch.object(
                    wrapper.os, "replace", side_effect=interrupt_third_replace):
                with self.assertRaises(OSError):
                    wrapper._refresh_healthy_pair(launcher, prestate)

            wrapper._recover_rewrap(launcher)

            backups = list(output.glob(
                ".build_script_build-deadbeef.previous-*.real"))
            self.assertEqual(1, len(backups))
            self.assertEqual(old_bytes, backups[0].read_bytes())
            self.assertEqual(new_bytes, real.read_bytes())
            self.assertTrue(wrapper._is_exact_launcher(launcher, real))
            self.assertFalse(wrapper._rewrap_manifest_path(launcher).exists())

    def test_prepared_intent_recovers_when_wrapper_dies_after_rustc(self):
        """Recover refreshed raw output using identity saved before rustc."""
        wrapper = _load_wrapper_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            launcher = output / "build_script_build-deadbeef"
            real = launcher.with_name(f"{launcher.name}.real")
            old_bytes = _macho_executable_bytes() + b"old"
            new_bytes = _macho_executable_bytes() + b"new"
            real.write_bytes(old_bytes)
            real.chmod(0o755)
            launcher.write_text(
                wrapper._launcher_source(real), encoding="utf-8")
            launcher.chmod(0o755)
            prestate = wrapper._healthy_pair_state(launcher)
            wrapper._prepare_healthy_pair(launcher, prestate)

            launcher.write_bytes(new_bytes)
            launcher.chmod(0o755)
            wrapper._recover_rewrap(launcher)

            backups = list(output.glob(
                ".build_script_build-deadbeef.previous-*.real"))
            self.assertEqual(1, len(backups))
            self.assertEqual(old_bytes, backups[0].read_bytes())
            self.assertEqual(new_bytes, real.read_bytes())
            self.assertTrue(wrapper._is_exact_launcher(launcher, real))

    def test_prepared_rollback_recovers_after_launcher_temporary_fsync(self):
        """Finish rollback after interruption immediately before replacement."""
        wrapper = _load_wrapper_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            launcher = output / "build_script_build-deadbeef"
            real = launcher.with_name(f"{launcher.name}.real")
            real.write_bytes(_macho_executable_bytes() + b"old")
            real.chmod(0o755)
            launcher.write_text(
                wrapper._launcher_source(real), encoding="utf-8")
            launcher.chmod(0o755)
            prestate = wrapper._healthy_pair_state(launcher)
            wrapper._prepare_healthy_pair(launcher, prestate)
            launcher.write_bytes(b"partial compiler output")
            launcher.chmod(0o755)
            original_replace = os.replace

            def interrupt_launcher_replace(source, destination):
                """Stop after the rollback launcher temporary is durable."""
                if Path(source).name.endswith(".launcher.tmp"):
                    raise OSError("simulated interruption")
                return original_replace(source, destination)

            with mock.patch.object(
                    wrapper.os, "replace",
                    side_effect=interrupt_launcher_replace):
                with self.assertRaises(OSError):
                    wrapper._recover_rewrap(
                        launcher, promote_prepared=False)

            temporary = output / f".{launcher.name}.launcher.tmp"
            self.assertTrue(temporary.is_file())
            wrapper._recover_rewrap(launcher)
            wrapper._recover_rewrap(launcher)

            self.assertTrue(wrapper._is_exact_launcher(launcher, real))
            self.assertFalse(temporary.exists())
            self.assertFalse(wrapper._rewrap_manifest_path(launcher).exists())

    def test_prepared_rollback_rejects_untrusted_launcher_temporary(self):
        """Fail closed when the durable rollback temporary is not exact."""
        for anomaly in ("content", "symlink"):
            with self.subTest(anomaly=anomaly), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                output = Path(temporary_directory).resolve()
                launcher = output / "build_script_build-deadbeef"
                real = launcher.with_name(f"{launcher.name}.real")
                real.write_bytes(_macho_executable_bytes() + b"old")
                real.chmod(0o755)
                wrapper = _load_wrapper_module()
                launcher.write_text(
                    wrapper._launcher_source(real), encoding="utf-8")
                launcher.chmod(0o755)
                prestate = wrapper._healthy_pair_state(launcher)
                wrapper._prepare_healthy_pair(launcher, prestate)
                launcher.write_bytes(b"partial compiler output")
                launcher.chmod(0o755)
                temporary = output / f".{launcher.name}.launcher.tmp"
                if anomaly == "content":
                    temporary.write_text("not a launcher", encoding="utf-8")
                    temporary.chmod(0o755)
                else:
                    temporary.symlink_to(real)
                compiler = self._write_compiler(
                    output, "raise SystemExit(0)\n")

                result = subprocess.run([
                    sys.executable, str(WRAPPER_PATH), str(compiler),
                    "--crate-name", "build_script_build",
                    "-C", "extra-filename=-deadbeef",
                    "--out-dir", str(output),
                ], check=False)

                self.assertEqual(125, result.returncode)
                self.assertTrue(wrapper._rewrap_manifest_path(launcher).exists())

    @unittest.skipUnless(
        sys.platform == "darwin" and platform.machine() == "arm64",
        "requires local arm64 clang and LLDB")
    def test_real_healthy_unit_recompile_launcher_runs_new_binary(self):
        """Compile one unit twice and launch the refreshed binary via LLDB."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            generation = root / "generation"
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                f"generation = pathlib.Path({str(generation)!r})\n"
                "number = int(generation.read_text()) + 1 "
                "if generation.exists() else 1\n"
                "generation.write_text(str(number))\n"
                "source = out / 'generation.c'\n"
                "source.write_text('#include <stdio.h>\\nint main(void) { "
                "printf(\"generation-' + str(number) + '\\\\n\"); "
                "return 0; }\\n')\n"
                "result = __import__('subprocess').run(["
                "'/usr/bin/xcrun', 'clang', '-arch', 'arm64', "
                "str(source), '-o', str(target)])\n"
                "raise SystemExit(result.returncode)\n"))
            command = [
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "-C", "extra-filename=-deadbeef",
                "--out-dir", str(output),
            ]

            first = subprocess.run(
                command, check=False, capture_output=True, text=True)
            second = subprocess.run(
                command, check=False, capture_output=True, text=True)
            launched = subprocess.run(
                [str(output / "build_script_build-deadbeef")],
                check=False, capture_output=True, text=True, timeout=20)

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(0, launched.returncode, launched.stderr)
        self.assertIn("generation-2", launched.stdout)

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

    def test_healthy_pair_compile_failure_restores_prior_launcher(self):
        """Keep the old runnable pair when rustc returns a normal failure."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "out"
            output.mkdir()
            output = output.resolve()
            launcher = output / "build_script_build-deadbeef"
            real = launcher.with_name(f"{launcher.name}.real")
            real.write_bytes(_macho_executable_bytes() + b"old")
            real.chmod(0o755)
            wrapper = _load_wrapper_module()
            launcher.write_text(
                wrapper._launcher_source(real), encoding="utf-8")
            launcher.chmod(0o755)
            compiler = self._write_compiler(root, (
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--out-dir') + 1])\n"
                "target = out / 'build_script_build-deadbeef'\n"
                "target.write_bytes(b'partial')\n"
                "target.chmod(0o755)\n"
                "raise SystemExit(17)\n"))

            result = subprocess.run([
                sys.executable, str(WRAPPER_PATH), str(compiler),
                "--crate-name", "build_script_build",
                "-C", "extra-filename=-deadbeef",
                "--out-dir", str(output),
            ], check=False)

            self.assertEqual(17, result.returncode)
            self.assertTrue(wrapper._is_exact_launcher(launcher, real))
            self.assertEqual(
                _macho_executable_bytes() + b"old", real.read_bytes())
            self.assertFalse(wrapper._rewrap_manifest_path(launcher).exists())

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
                f"pathlib.Path({str(log)!r}).write_text("
                "json.dumps(sys.argv[1:]))\n"
                "raise SystemExit(23)\n",
                encoding="utf-8")
            fake_xcrun.chmod(fake_xcrun.stat().st_mode | stat.S_IXUSR)

            status = wrapper.run_build_script(
                build_script, ("argument with spaces", "literal'quote"),
                xcrun=fake_xcrun)
            invocation = json.loads(log.read_text(encoding="utf-8"))

        self.assertEqual(23, status)
        separator = invocation.index("--")
        lldb_arguments = invocation[:separator]
        arguments = invocation[separator + 1:]
        launch_index = lldb_arguments.index("process launch --stop-at-entry")
        policy_index = next(
            index for index, value in enumerate(lldb_arguments)
            if "GetNumSignals" in value)
        continue_index = lldb_arguments.index("process continue")
        self.assertLess(launch_index, policy_index)
        self.assertLess(policy_index, continue_index)
        self.assertIn("SIGSTOP", lldb_arguments[policy_index])
        self.assertIn("SetShouldSuppress(n,False)",
                      lldb_arguments[policy_index])
        crash_hook_index = lldb_arguments.index("-k")
        status_script = lldb_arguments[crash_hook_index - 1]
        self.assertEqual(status_script,
                         lldb_arguments[crash_hook_index + 1])
        self.assertIn("p.GetState()==lldb.eStateExited", status_script)
        self.assertIn("else 125", status_script)
        self.assertEqual(
            [str(build_script.resolve()),
             "argument with spaces", "literal'quote"],
            arguments)

    def test_negative_lldb_status_uses_shell_signal_convention(self):
        """Normalize an xcrun or LLDB signal into 128-plus-signal status."""
        wrapper = _load_wrapper_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            build_script = root / "build_script_build-deadbeef.real"
            build_script.write_bytes(_macho_executable_bytes())
            build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)
            fake_xcrun = root / "signaled xcrun.py"
            fake_xcrun.write_text(
                "#!/usr/bin/env python3\n"
                "import os, signal\n"
                "os.kill(os.getpid(), signal.SIGTERM)\n",
                encoding="utf-8")
            fake_xcrun.chmod(fake_xcrun.stat().st_mode | stat.S_IXUSR)

            status = wrapper.run_build_script(
                build_script, (), xcrun=fake_xcrun)

        self.assertEqual(143, status)

    @unittest.skipUnless(
        sys.platform == "darwin" and platform.machine() == "arm64",
        "requires the local arm64 macOS LLDB runtime")
    def test_real_lldb_maps_normal_and_signal_exits(self):
        """Map normal and representative debugger-intercepted signal exits."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "build_script.c"
            source.write_text(
                "#include <signal.h>\n"
                "#include <string.h>\n"
                "int main(int argc, char **argv) {\n"
                "  if (argc > 1 && strcmp(argv[1], \"term\") == 0) "
                "raise(SIGTERM);\n"
                "  if (argc > 1 && strcmp(argv[1], \"kill\") == 0) "
                "raise(SIGKILL);\n"
                "  if (argc > 1 && strcmp(argv[1], \"trap\") == 0) "
                "raise(SIGTRAP);\n"
                "  if (argc > 1 && strcmp(argv[1], \"quit\") == 0) "
                "raise(SIGQUIT);\n"
                "  if (argc > 1 && strcmp(argv[1], \"tstp\") == 0) "
                "raise(SIGTSTP);\n"
                "  if (argc > 1 && strcmp(argv[1], \"ttin\") == 0) "
                "raise(SIGTTIN);\n"
                "  if (argc > 1 && strcmp(argv[1], \"ttou\") == 0) "
                "raise(SIGTTOU);\n"
                "  return 7;\n"
                "}\n",
                encoding="utf-8")
            executable = root / "build_script_build-deadbeef.real"
            compile_result = subprocess.run([
                "/usr/bin/xcrun", "clang", "-arch", "arm64",
                str(source), "-o", str(executable),
            ], check=False, capture_output=True, text=True)
            self.assertEqual(0, compile_result.returncode, compile_result.stderr)

            statuses = {}
            diagnostics = {}
            for mode in (
                    "normal", "term", "kill", "trap", "quit",
                    "tstp", "ttin", "ttou"):
                result = subprocess.run([
                    str(WRAPPER_PATH), "--run-build-script",
                    str(executable), mode,
                ], check=False, capture_output=True, text=True, timeout=20)
                statuses[mode] = result.returncode
                diagnostics[mode] = result.stderr

        self.assertEqual(
            {"normal": 7, "term": 143, "kill": 137,
             "trap": 133, "quit": 131, "tstp": 125,
             "ttin": 125, "ttou": 125}, statuses, diagnostics)

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
