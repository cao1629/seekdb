#!/usr/bin/env python3
"""Contract tests for the generic macOS LLDB executable launcher."""

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parents[1]
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import macos_lldb_launcher as launcher  # noqa: E402
import run_mysqltest_phase as phase  # noqa: E402


def _load_sdb():
    """Load the tracked sdb module for direct command contract tests."""
    path = REPOSITORY_ROOT / ".github/script/seekdb/sdb.py"
    spec = importlib.util.spec_from_file_location("seekdb_sdb_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacosLldbLauncherTest(unittest.TestCase):
    """Protect launcher selection, argv structure, and exit propagation."""

    def test_lldb_command_preserves_opaque_arguments(self):
        """Keep spaces and shell metacharacters as individual argv values."""
        binary = Path("/tmp/snapshot with spaces/seekdb")
        arguments = ("plain", "two words", "$(touch nope)", "'quoted'")

        command = launcher.lldb_command(binary, arguments)

        separator = command.index("--")
        self.assertEqual(str(binary), command[separator + 1])
        self.assertEqual(list(arguments), command[separator + 2:])

    def test_lldb_command_routes_target_stdin_without_exposing_it_to_lldb(self):
        """Route a dedicated input fd only through the target launch command."""
        command = launcher.lldb_command(
            Path("/snapshot/seekdb"), (), target_stdin_fd=17)

        launch_command = command[command.index("-o") + 1]
        self.assertIn(" -i /dev/fd/17", launch_command)

    def test_partial_standard_stream_duplication_closes_acquired_fds(self):
        """Close earlier duplicates when a later standard-stream dup fails."""
        with mock.patch.object(
                launcher.os, "dup", side_effect=[31, OSError("dup failed")]), \
                mock.patch.object(launcher.os, "close") as close:
            with self.assertRaises(OSError):
                launcher._duplicate_standard_streams()

        close.assert_called_once_with(31)

    def test_direct_sigkill_probe_selects_tracked_launcher(self):
        """Use LLDB for every host tool after one direct policy SIGKILL."""
        calls = []

        def killed(command, _cwd, _deadline):
            """Simulate the macOS execution-policy kill without a shell."""
            calls.append(tuple(command))
            return subprocess.CompletedProcess(command, 137, b"", b"")

        binaries = {
            name: Path("/snapshot") / name
            for name in phase.HOST_BINARY_NAMES
        }
        with mock.patch.object(phase.sys, "platform", "darwin"), \
                mock.patch.object(
                    phase.platform, "mac_ver",
                    return_value=("26.0", ("", "", ""), "arm64")), \
                mock.patch.object(
                    phase.macos_lldb_launcher, "validate_lldb") as validate:
            selected = phase._select_host_executable_launcher(
                binaries, process_runner=killed)

        self.assertEqual(phase.MACOS_LLDB_LAUNCHER, selected)
        self.assertEqual([("/snapshot/seekdb", "--help")], calls)
        validate.assert_called_once()

    def test_normal_macos_and_linux_keep_direct_execution(self):
        """Avoid LLDB when platform policy does not kill direct host tools."""
        binaries = {
            name: Path("/snapshot") / name
            for name in phase.HOST_BINARY_NAMES
        }

        def succeeds(command, _cwd, _deadline):
            """Return one normal direct executable probe result."""
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with mock.patch.object(phase.sys, "platform", "darwin"), \
                mock.patch.object(
                    phase.platform, "mac_ver",
                    return_value=("26.0", ("", "", ""), "arm64")):
            self.assertIsNone(phase._select_host_executable_launcher(
                binaries, process_runner=succeeds))
        with mock.patch.object(phase.sys, "platform", "linux"):
            self.assertIsNone(phase._select_host_executable_launcher(
                binaries, process_runner=mock.Mock()))

    def test_sdb_launcher_command_and_marker_bind_real_seekdb(self):
        """Prefix launch argv while retaining the real snapshot in the marker."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "binaries/seekdb"
            launch_script = root / "macos_lldb_launcher.py"
            binary.parent.mkdir()
            binary.write_bytes(b"binary")
            binary.chmod(0o500)
            launch_script.write_text("launcher", encoding="utf-8")
            launch_script.chmod(0o500)
            base_dir = root / "instance"
            args = argparse.Namespace(
                binary=str(binary), launcher=str(launch_script), port=2881,
                nodaemon=False, parameter=[])

            command = sdb.build_start_command(args, base_dir)
            sdb.prepare_instance_directory(base_dir, binary)

            self.assertEqual(
                [sys.executable, str(launch_script), "--binary",
                 str(binary), "--"], command[:5])
            self.assertIn("--base-dir={}".format(base_dir), command)
            self.assertEqual(binary, sdb.read_instance_binary(base_dir))
            with mock.patch.object(
                    sdb, "read_process_arguments",
                    return_value=[
                        str(binary), "--base-dir={}".format(base_dir)]):
                self.assertTrue(sdb.process_matches_instance(
                    123, base_dir, binary))
            with mock.patch.object(
                    sdb, "read_process_arguments",
                    return_value=[
                        str(launch_script), "--binary", str(binary),
                        "--base-dir={}".format(base_dir)]):
                self.assertFalse(sdb.process_matches_instance(
                    123, base_dir, binary))

    def test_timeout_terminates_and_kills_the_lldb_process_group(self):
        """Bound timeout cleanup even when the LLDB group ignores SIGTERM."""
        process = mock.Mock(pid=4321)
        process.wait.side_effect = [
            subprocess.TimeoutExpired("lldb", 1),
            subprocess.TimeoutExpired("lldb", 2),
            0,
        ]
        with mock.patch.object(launcher, "validate_lldb"), \
                mock.patch.object(
                    launcher, "lldb_command", return_value=["lldb"]), \
                mock.patch.object(
                    launcher.subprocess, "Popen",
                    return_value=process) as popen, \
                mock.patch.object(launcher.os, "killpg") as killpg:
            result = launcher.launch(
                Path("/snapshot/seekdb"), (), timeout_seconds=1)

        self.assertEqual(124, result)
        self.assertEqual(
            [mock.call(4321, signal.SIGTERM),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)
        popen_options = popen.call_args.kwargs
        self.assertEqual(subprocess.DEVNULL, popen_options["stdin"])
        self.assertEqual(3, len(popen_options["pass_fds"]))

    def test_interrupt_terminates_and_kills_the_lldb_process_group(self):
        """Clean the LLDB group before propagating an interactive interrupt."""
        process = mock.Mock(pid=4321)
        process.wait.side_effect = [
            KeyboardInterrupt(),
            subprocess.TimeoutExpired("lldb", 2),
            0,
        ]
        with mock.patch.object(launcher, "validate_lldb"), \
                mock.patch.object(
                    launcher, "lldb_command", return_value=["lldb"]), \
                mock.patch.object(
                    launcher.subprocess, "Popen", return_value=process), \
                mock.patch.object(launcher.os, "killpg") as killpg:
            with self.assertRaises(KeyboardInterrupt):
                launcher.launch(
                    Path("/snapshot/seekdb"), (), timeout_seconds=1)

        self.assertEqual(
            [mock.call(4321, signal.SIGTERM),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)

    def test_cleanup_kills_the_group_after_the_lldb_leader_exits(self):
        """Kill remaining descendants even when the LLDB leader exits on TERM."""
        process = mock.Mock(pid=4321)
        process.wait.return_value = 0

        with mock.patch.object(launcher.os, "killpg") as killpg:
            launcher._terminate_group(process)

        self.assertEqual(
            [mock.call(4321, signal.SIGTERM),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)
        self.assertEqual(2, process.wait.call_count)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("cc"),
        "requires macOS, clang, and LLDB")
    def test_real_lldb_propagates_exit_and_signal_status(self):
        """Map normal exit, SIGTERM, and SIGKILL from a real tiny Mach-O."""
        source = r'''
#include <signal.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "term") == 0) raise(SIGTERM);
  if (argc > 1 && strcmp(argv[1], "kill") == 0) raise(SIGKILL);
  return argc > 1 ? atoi(argv[1]) : 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            program = root / "tiny"
            completed = subprocess.run(
                ["cc", "-x", "c", "-o", str(program), "-"],
                input=source.encode("utf-8"), capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            program.chmod(0o500)
            validated = launcher.validate_binary(str(program))

            self.assertEqual(7, launcher.launch(validated, ("7",),
                                                timeout_seconds=60))
            self.assertEqual(128 + signal.SIGTERM,
                             launcher.launch(validated, ("term",),
                                             timeout_seconds=60))
            self.assertEqual(128 + signal.SIGKILL,
                             launcher.launch(validated, ("kill",),
                                             timeout_seconds=60))

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("cc"),
        "requires macOS, clang, and LLDB")
    def test_real_lldb_preserves_pipe_file_devnull_tty_and_eof_stdin(self):
        """Round-trip target stdin while LLDB itself remains on DEVNULL."""
        source = r'''
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
  char buffer[256];
  if (argc > 1 && strcmp(argv[1], "fgets") == 0) {
    if (fgets(buffer, sizeof(buffer), stdin) == NULL) {
      fputs("EOF", stdout);
      return 0;
    }
    fputs(buffer, stdout);
    return 0;
  }
  size_t count;
  while ((count = fread(buffer, 1, sizeof(buffer), stdin)) > 0) {
    if (fwrite(buffer, 1, count, stdout) != count) return 3;
  }
  return ferror(stdin) ? 4 : 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            program = root / "stdin-roundtrip"
            compiled = subprocess.run(
                ["cc", "-x", "c", "-o", str(program), "-"],
                input=source.encode("utf-8"), capture_output=True,
                check=False)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            program.chmod(0o500)
            command = [
                sys.executable, str(phase.MACOS_LLDB_LAUNCHER),
                "--binary", str(program), "--timeout", "60", "--",
            ]

            piped = subprocess.run(
                command, input=b"pipe input\n", capture_output=True,
                timeout=90, check=False)
            self.assertEqual(0, piped.returncode, piped.stderr)
            self.assertEqual(b"pipe input\n", piped.stdout)

            input_file = root / "init.sql"
            input_file.write_bytes(b"file input\n")
            with input_file.open("rb") as stream:
                from_file = subprocess.run(
                    command, stdin=stream, capture_output=True,
                    timeout=90, check=False)
            self.assertEqual(0, from_file.returncode, from_file.stderr)
            self.assertEqual(b"file input\n", from_file.stdout)

            from_devnull = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=90, check=False)
            self.assertEqual(0, from_devnull.returncode, from_devnull.stderr)
            self.assertEqual(b"", from_devnull.stdout)

            empty = subprocess.run(
                [*command, "fgets"], input=b"", capture_output=True,
                timeout=90, check=False)
            self.assertEqual(0, empty.returncode, empty.stderr)
            self.assertEqual(b"EOF", empty.stdout)

            master_fd, slave_fd = os.openpty()
            try:
                tty_process = subprocess.Popen(
                    [*command, "fgets"], stdin=slave_fd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                os.close(slave_fd)
                slave_fd = -1
                os.write(master_fd, b"tty input\n")
                tty_stdout, tty_stderr = tty_process.communicate(timeout=90)
            finally:
                os.close(master_fd)
                if slave_fd >= 0:
                    os.close(slave_fd)
            self.assertEqual(0, tty_process.returncode, tty_stderr)
            self.assertEqual(b"tty input\n", tty_stdout)

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS and LLDB")
    def test_current_host_snapshots_execute_read_only_options_via_lldb(self):
        """Run the three current snapshotted tools without the direct SIGKILL."""
        sources = {
            "seekdb": REPOSITORY_ROOT / "build_release/src/observer/seekdb",
            "obclient": REPOSITORY_ROOT / "deps/3rd/u01/obclient/bin/obclient",
            "mysqltest": REPOSITORY_ROOT / "deps/3rd/u01/obclient/bin/mysqltest",
        }
        if not all(path.is_file() for path in sources.values()):
            self.skipTest("current canonical host outputs are unavailable")
        options = {
            "seekdb": "--help", "obclient": "--version",
            "mysqltest": "--help",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle = phase.prepare_host_binary_snapshots(
                sources, root / "mysqltest-host")
            phase.validate_host_binary_snapshots(bundle)
            for name in phase.HOST_BINARY_NAMES:
                with self.subTest(binary=name):
                    completed = subprocess.run([
                        sys.executable, str(phase.MACOS_LLDB_LAUNCHER),
                        "--binary", str(bundle.snapshot_paths[name]),
                        "--timeout", "60", "--", options[name],
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=90, check=False)
                    self.assertNotIn(
                        completed.returncode, {125, 128 + signal.SIGKILL})
                    debugger_output = completed.stdout + completed.stderr
                    self.assertNotIn(b"(lldb)", debugger_output)
                    self.assertNotIn(b"Current executable set", debugger_output)


if __name__ == "__main__":
    unittest.main()
