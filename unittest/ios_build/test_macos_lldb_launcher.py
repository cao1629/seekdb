#!/usr/bin/env python3
"""Contract tests for the generic macOS LLDB executable launcher."""

import argparse
import ctypes
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
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


def _wait_for_pid_file(path: Path, timeout_seconds: float = 30) -> int:
    """Return a child pid after a bounded wait for its durable marker."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="ascii").strip()
            if value:
                return int(value)
        except (FileNotFoundError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("target pid marker was not created")


def _pid_exists(pid: int) -> bool:
    """Return whether one process id has any process-table entry, including Z."""
    completed = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True, check=False, text=True)
    return bool(completed.stdout.strip())


def _wait_for_lldb_session_leader(
        parent_pid: int, timeout_seconds: float = 15) -> int:
    """Return only the direct LLDB child that leads its own session group."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,command="],
            capture_output=True, check=False, text=True)
        for line in completed.stdout.splitlines():
            values = line.split(maxsplit=3)
            if len(values) != 4:
                continue
            pid, ppid, pgid = (int(value) for value in values[:3])
            command = values[3]
            if (ppid == parent_pid and pgid == pid
                    and "lldb" in command and "--no-lldbinit" in command):
                return pid
        time.sleep(0.05)
    raise AssertionError("LLDB session leader was not created")


def _wait_for_lldb_target(
        lldb_pid: int, executable: Path, timeout_seconds: float = 15) -> int:
    """Return the expected target from one LLDB descendant process tree."""
    deadline = time.monotonic() + timeout_seconds
    expected_prefix = str(executable) + " "
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,stat=,command="],
            capture_output=True, check=False, text=True)
        parents = {}
        commands = {}
        states = {}
        for line in completed.stdout.splitlines():
            values = line.split(maxsplit=3)
            if len(values) != 4:
                continue
            pid, ppid = (int(value) for value in values[:2])
            parents[pid] = ppid
            states[pid] = values[2]
            commands[pid] = values[3]
        for pid, command in commands.items():
            if not (command == str(executable)
                    or command.startswith(expected_prefix)) \
                    or "T" in states[pid]:
                continue
            ancestor = parents.get(pid)
            while ancestor not in (None, 0, 1, lldb_pid):
                ancestor = parents.get(ancestor)
            if ancestor == lldb_pid:
                return pid
        time.sleep(0.05)
    raise AssertionError("LLDB target child was not created")


def _close_process_pipes(process: subprocess.Popen) -> None:
    """Close captured process streams owned by one test parent."""
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


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

    def test_interrupted_descriptor_cleanup_never_recloses_removed_fd(self):
        """Remove fd ownership before close so signal cleanup cannot double-close."""
        descriptors = [31, 32]
        with mock.patch.object(
                launcher.os, "close",
                side_effect=[launcher._ExternalTermination(signal.SIGTERM),
                             None]) as close:
            with self.assertRaises(launcher._ExternalTermination):
                launcher._close_descriptors(descriptors)
            launcher._close_descriptors(descriptors)

        self.assertEqual([mock.call(32), mock.call(31)], close.call_args_list)

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
            ready_args = argparse.Namespace(
                client=str(binary), launcher=str(launch_script),
                host="127.0.0.1", port=2881, user="root")
            ready_command = sdb.build_ready_command(
                ready_args, launcher_timeout=3.75)
            self.assertEqual(
                ["--timeout", "3.75", "--"], ready_command[4:7])
            with mock.patch.object(
                    sdb, "read_process_identity",
                    return_value=(
                        binary,
                        [str(binary), "--base-dir={}".format(base_dir)])):
                self.assertTrue(sdb.process_matches_instance(
                    123, base_dir, binary))
            with mock.patch.object(
                    sdb, "read_process_identity",
                    return_value=(
                        launch_script,
                        [str(launch_script), "--binary", str(binary),
                         "--base-dir={}".format(base_dir)])):
                self.assertFalse(sdb.process_matches_instance(
                    123, base_dir, binary))

    def test_sdb_macos_procargs_parser_preserves_opaque_argv(self):
        """Preserve spaces, quotes, Unicode, and empty argv entries losslessly."""
        sdb = _load_sdb()
        arguments = [
            "/tmp/launcher path/Python", "quoted ' launcher.py", "",
            "snowman-☃", '--parameter=a "b"',
        ]
        argc = ctypes.c_int(len(arguments))
        prefix = ctypes.string_at(
            ctypes.byref(argc), ctypes.sizeof(argc))
        payload = prefix + b"/tmp/launcher path/Python\0\0\0" + b"\0".join(
            os.fsencode(value) for value in arguments) + b"\0KEY=value\0"

        executable, parsed = sdb._parse_macos_procargs(payload)

        self.assertEqual(Path("/tmp/launcher path/Python"), executable)
        self.assertEqual(arguments, parsed)

    def test_sdb_macos_procargs_parser_rejects_invalid_payloads(self):
        """Reject unsafe argument counts and truncated NUL-delimited data."""
        sdb = _load_sdb()

        def encoded_argc(value):
            """Return one native C integer for a synthetic sysctl payload."""
            count = ctypes.c_int(value)
            return ctypes.string_at(
                ctypes.byref(count), ctypes.sizeof(count))

        invalid = (
            encoded_argc(0) + b"/bin/tool\0tool\0",
            encoded_argc(sdb.MAX_PROCESS_ARGUMENT_COUNT + 1)
            + b"/bin/tool\0tool\0",
            encoded_argc(2) + b"/bin/tool\0\0tool\0unterminated",
            encoded_argc(1) + b"relative-tool\0\0tool\0",
        )
        for payload in invalid:
            with self.subTest(payload=payload[:24]):
                with self.assertRaises(RuntimeError):
                    sdb._parse_macos_procargs(payload)

    @unittest.skipUnless(sys.platform == "darwin", "requires KERN_PROCARGS2")
    def test_sdb_macos_procargs_reads_current_process_and_missing_pid(self):
        """Read a live argv without shell parsing and classify a vanished PID."""
        sdb = _load_sdb()
        executable, arguments = sdb._read_macos_process_identity(os.getpid())
        self.assertTrue(executable.is_absolute())
        self.assertTrue(arguments)
        if sys.argv[1:]:
            self.assertEqual(sys.argv[1:], arguments[-len(sys.argv[1:]):])
        elif Path(sys.argv[0]).exists():
            self.assertEqual(
                Path(sys.argv[0]).resolve(), Path(arguments[-1]).resolve())
        self.assertIsNone(sdb._read_macos_process_identity(99999999))

        def permission_denied(mib, output, _output_size):
            """Return a valid ARG_MAX followed by a denied process read."""
            if tuple(mib) == (sdb.MACOS_CTL_KERN, sdb.MACOS_KERN_ARGMAX):
                output._obj.value = 4096
                return 0, 0, ctypes.sizeof(ctypes.c_int)
            return -1, 1, 0

        with mock.patch.object(
                sdb, "_macos_sysctl", side_effect=permission_denied):
            with self.assertRaisesRegex(RuntimeError, "errno=1"):
                sdb._read_macos_process_identity(os.getpid())

    def test_sdb_launcher_marker_rejects_symlink_tamper_and_pid_reuse(self):
        """Fail closed before signaling from an unsafe launcher marker."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            launch_script = root / "macos_lldb_launcher.py"
            binary.write_bytes(b"binary")
            launch_script.write_text("launcher", encoding="utf-8")
            base_dir = root / "instance"
            sdb.prepare_instance_directory(base_dir, binary)
            command = [
                sys.executable, str(launch_script), "--binary", str(binary),
                "--", "--base-dir={}".format(base_dir), "--port=2881",
            ]
            record = {
                "version": sdb.LAUNCHER_MARKER_VERSION,
                "pid": os.getpid(),
                "start_identity": "reused-process-identity",
                "process_executable": str(Path(sys.executable).resolve()),
                "launcher_executable": str(launch_script),
                "target_binary": str(binary),
                "base_dir": str(base_dir),
                "argv": command,
            }
            sdb.write_launcher_marker(base_dir, record)
            with mock.patch.object(sdb.os, "kill", wraps=os.kill) as kill:
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    sdb.cleanup_launcher(base_dir, binary)
                self.assertFalse(any(
                    call.args == (os.getpid(), signal.SIGTERM)
                    for call in kill.call_args_list))

            marker = base_dir / "run" / sdb.LAUNCHER_MARKER_NAME
            tampered = dict(record)
            tampered["base_dir"] = str(root / "other-instance")
            marker.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                sdb.read_launcher_marker(base_dir, binary)
            marker.unlink()
            victim = root / "victim"
            victim.write_text("unchanged", encoding="utf-8")
            marker.symlink_to(victim)
            with self.assertRaises(OSError):
                sdb.read_launcher_marker(base_dir, binary)
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))

    def test_sdb_marker_write_failure_stops_new_launcher(self):
        """Invoke precise launcher cleanup when durable persistence fails."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            launch_script = root / "launcher.py"
            binary.write_bytes(b"binary")
            launch_script.write_text("launcher", encoding="utf-8")
            base_dir = root / "instance"
            args = argparse.Namespace(
                base_dir=str(base_dir), binary=str(binary),
                launcher=str(launch_script), port=2881, nodaemon=True,
                parameter=[])
            process = mock.Mock(pid=9911)
            record = {"start_identity": "start-id"}
            with mock.patch.object(
                    sdb, "spawn_detached", return_value=process), \
                    mock.patch.object(
                        sdb, "_launcher_record", return_value=record), \
                    mock.patch.object(
                        sdb, "write_launcher_marker",
                        side_effect=OSError("marker failure")), \
                    mock.patch.object(sdb, "_cleanup_new_launcher") as cleanup:
                self.assertEqual(1, sdb.command_start(args))
            cleanup.assert_called_once_with(
                process, sdb.build_start_command(args, base_dir), "start-id")

    def test_sdb_start_rejects_unsafe_preexisting_launcher_markers(self):
        """Reject unsafe marker objects before creating a detached process."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            launcher_script = root / "launcher.py"
            binary.write_bytes(b"binary")
            launcher_script.write_text("launcher", encoding="utf-8")
            for kind in (
                    "malformed", "symlink", "fifo", "oversize", "foreign"):
                with self.subTest(kind=kind):
                    base_dir = root / kind
                    sdb.prepare_instance_directory(base_dir, binary)
                    run_dir = base_dir / "run"
                    run_dir.mkdir()
                    marker = run_dir / sdb.LAUNCHER_MARKER_NAME
                    victim = root / "{}-victim".format(kind)
                    if kind == "malformed":
                        marker.write_bytes(b"{not-json\n")
                    elif kind == "symlink":
                        victim.write_text("unchanged", encoding="utf-8")
                        marker.symlink_to(victim)
                    elif kind == "fifo":
                        os.mkfifo(marker)
                    elif kind == "foreign":
                        marker.write_text(json.dumps({
                            "version": sdb.LAUNCHER_MARKER_VERSION,
                            "pid": 99999999,
                            "start_identity": "foreign-start",
                            "process_executable": str(
                                Path(sys.executable).resolve()),
                            "launcher_executable": str(launcher_script),
                            "target_binary": str(root / "other-seekdb"),
                            "base_dir": str(base_dir),
                            "argv": [
                                sys.executable, str(launcher_script),
                                "--binary", str(root / "other-seekdb"), "--",
                                "--base-dir={}".format(base_dir),
                            ],
                        }), encoding="utf-8")
                    else:
                        marker.write_bytes(
                            b"x" * (sdb.LAUNCHER_MARKER_MAX_BYTES + 1))
                    args = argparse.Namespace(
                        base_dir=str(base_dir), binary=str(binary),
                        launcher=str(launcher_script), port=2881,
                        nodaemon=True, parameter=[])
                    with mock.patch.object(sdb, "spawn_detached") as spawn:
                        self.assertEqual(1, sdb.command_start(args))
                    spawn.assert_not_called()
                    if kind == "symlink":
                        self.assertTrue(marker.is_symlink())
                        self.assertEqual(
                            "unchanged", victim.read_text(encoding="utf-8"))
                    elif kind == "fifo":
                        self.assertTrue(stat.S_ISFIFO(marker.lstat().st_mode))
                    elif kind == "malformed":
                        self.assertEqual(b"{not-json\n", marker.read_bytes())
                    elif kind == "oversize":
                        self.assertEqual(
                            sdb.LAUNCHER_MARKER_MAX_BYTES + 1,
                            marker.stat().st_size)
                    else:
                        self.assertIn(
                            "other-seekdb", marker.read_text(encoding="utf-8"))

    def test_sdb_preflight_cleans_exited_marker_and_rejects_duplicate(self):
        """Remove only exited ownership and preserve a validated live owner."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            launcher_script = root / "launcher.py"
            binary.write_bytes(b"binary")
            launcher_script.write_text("launcher", encoding="utf-8")
            base_dir = root / "instance"
            sdb.prepare_instance_directory(base_dir, binary)
            command = [
                sys.executable, str(launcher_script), "--binary", str(binary),
                "--", "--base-dir={}".format(base_dir),
            ]
            record = {
                "version": sdb.LAUNCHER_MARKER_VERSION,
                "pid": 99999999,
                "start_identity": "exited-start",
                "process_executable": str(Path(sys.executable).resolve()),
                "launcher_executable": str(launcher_script),
                "target_binary": str(binary),
                "base_dir": str(base_dir),
                "argv": command,
            }
            sdb.write_launcher_marker(base_dir, record)
            with mock.patch.object(sdb, "process_exists", return_value=False):
                sdb.preflight_launcher_marker(base_dir, binary)
            marker = base_dir / "run" / sdb.LAUNCHER_MARKER_NAME
            self.assertFalse(marker.exists())

            record["pid"] = os.getpid()
            record["start_identity"] = "live-start"
            sdb.write_launcher_marker(base_dir, record)
            with mock.patch.object(sdb, "process_exists", return_value=True), \
                    mock.patch.object(
                        sdb, "_launcher_process_matches", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    sdb.preflight_launcher_marker(base_dir, binary)
            self.assertTrue(marker.is_file())

    def test_sdb_lifecycle_lock_is_stable_and_rejects_unsafe_objects(self):
        """Keep one lock inode across recreation and reject unsafe lock names."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base_dir = root / "stable-instance"
            binary = root / "seekdb"
            binary.write_bytes(b"binary")
            with self.assertRaises(KeyboardInterrupt):
                with sdb.lifecycle_lock(base_dir):
                    first = sdb.lifecycle_lock_path(base_dir).stat()
                    sdb.prepare_instance_directory(base_dir, binary)
                    raise KeyboardInterrupt()
            self.assertEqual(
                0, sdb.command_destroy(argparse.Namespace(base_dir=str(base_dir))))
            sdb.prepare_instance_directory(base_dir, binary)
            with sdb.lifecycle_lock(base_dir):
                second = sdb.lifecycle_lock_path(base_dir).stat()
            self.assertEqual(
                (first.st_dev, first.st_ino), (second.st_dev, second.st_ino))
            descriptor = os.open(sdb.lifecycle_lock_path(base_dir), os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)

            for kind in ("symlink", "fifo", "mode"):
                with self.subTest(kind=kind):
                    unsafe_base = root / "{}-instance".format(kind)
                    lock_path = sdb.lifecycle_lock_path(unsafe_base)
                    victim = root / "{}-lock-victim".format(kind)
                    if kind == "symlink":
                        victim.write_text("unchanged", encoding="utf-8")
                        lock_path.symlink_to(victim)
                    elif kind == "fifo":
                        os.mkfifo(lock_path)
                    else:
                        lock_path.write_bytes(b"tampered")
                        lock_path.chmod(0o644)
                    with self.assertRaises((OSError, RuntimeError)):
                        with sdb.lifecycle_lock(unsafe_base):
                            self.fail("unsafe lifecycle lock was accepted")
                    if kind == "symlink":
                        self.assertTrue(lock_path.is_symlink())
                        self.assertEqual(
                            "unchanged", victim.read_text(encoding="utf-8"))
                    elif kind == "fifo":
                        self.assertTrue(stat.S_ISFIFO(lock_path.lstat().st_mode))
                    else:
                        self.assertEqual(b"tampered", lock_path.read_bytes())

    def test_sdb_keyboard_interrupt_rolls_back_launcher_ownership(self):
        """Clean the detached process before replaying KeyboardInterrupt."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            launch_script = root / "launcher.py"
            binary.write_bytes(b"binary")
            launch_script.write_text("launcher", encoding="utf-8")
            base_dir = root / "instance"
            args = argparse.Namespace(
                base_dir=str(base_dir), binary=str(binary),
                launcher=str(launch_script), port=2881, nodaemon=True,
                parameter=[])
            process = mock.Mock(pid=9912)
            record = {"start_identity": "start-id"}
            with mock.patch.object(
                    sdb, "spawn_detached", return_value=process), \
                    mock.patch.object(
                        sdb, "_launcher_record", return_value=record), \
                    mock.patch.object(
                        sdb, "write_launcher_marker",
                        side_effect=KeyboardInterrupt()), \
                    mock.patch.object(sdb, "_cleanup_new_launcher") as cleanup:
                with self.assertRaises(KeyboardInterrupt):
                    sdb.command_start(args)
            cleanup.assert_called_once_with(
                process, sdb.build_start_command(args, base_dir), "start-id")

    def test_sdb_rollback_cleans_process_before_unknown_marker_parse(self):
        """Keep an unknown marker but always clean exact in-memory ownership."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            binary.write_bytes(b"binary")
            base_dir = root / "instance"
            sdb.prepare_instance_directory(base_dir, binary)
            run_dir = base_dir / "run"
            run_dir.mkdir()
            marker = run_dir / sdb.LAUNCHER_MARKER_NAME
            marker.write_bytes(b"{unknown-race\n")
            process = mock.Mock(pid=9913)
            command = ["python", "launcher"]
            with mock.patch.object(sdb, "_cleanup_new_launcher") as cleanup:
                with self.assertRaisesRegex(
                        RuntimeError, "durable marker cleanup"):
                    sdb._abort_launcher_start(
                        process, command, base_dir, binary, None)
            cleanup.assert_called_once_with(process, command, None)
            self.assertEqual(b"{unknown-race\n", marker.read_bytes())

    def test_sdb_stop_handles_managed_pid_and_launcher_marker_together(self):
        """Stop both lifecycle records before reporting one instance stopped."""
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "seekdb"
            binary.write_bytes(b"binary")
            base_dir = root / "instance"
            sdb.prepare_instance_directory(base_dir, binary)
            run_dir = base_dir / "run"
            run_dir.mkdir()
            (run_dir / "seekdb.pid").write_text("7788\n", encoding="ascii")
            args = argparse.Namespace(
                base_dir=str(base_dir), quiet=True, require_match=True)
            with mock.patch.object(
                    sdb, "process_exists", return_value=True), \
                    mock.patch.object(
                        sdb, "process_matches_instance", return_value=True), \
                    mock.patch.object(sdb, "terminate_pid") as terminate, \
                    mock.patch.object(sdb, "cleanup_launcher") as cleanup:
                self.assertEqual(0, sdb.command_stop(args))
            terminate.assert_called_once_with(7788, base_dir, binary)
            cleanup.assert_called_once_with(base_dir, binary)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("cc"),
        "requires macOS, clang, and LLDB")
    def test_sdb_destroy_cleans_launcher_before_delayed_managed_pid(self):
        """Destroy the full launcher chain before seekdb publishes its pid."""
        source = r'''
#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
  const char *base = NULL;
  int delay = 30;
  for (int i = 1; i < argc; ++i) {
    if (strncmp(argv[i], "--base-dir=", 11) == 0) base = argv[i] + 11;
    if (strcmp(argv[i], "--port=2883") == 0) delay = 0;
  }
  if (base == NULL) return 2;
  char path[4096];
  snprintf(path, sizeof(path), "%s/target-started.pid", base);
  FILE *started = fopen(path, "w");
  if (started == NULL) return 3;
  fprintf(started, "%d\n", getpid());
  if (fclose(started) != 0) return 4;
  sleep(delay);
  snprintf(path, sizeof(path), "%s/run/seekdb.pid", base);
  FILE *managed = fopen(path, "w");
  if (managed == NULL) return 5;
  fprintf(managed, "%d\n", getpid());
  fclose(managed);
  sleep(300);
  return 0;
}
'''
        sdb_script = REPOSITORY_ROOT / ".github/script/seekdb/sdb.py"
        sdb = _load_sdb()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary_dir = root / "binary dir ' ☃"
            binary_dir.mkdir()
            program = binary_dir / 'delayed " seekdb'
            launcher_dir = root / 'launcher dir " ☃'
            launcher_dir.mkdir()
            launcher_copy = launcher_dir / "macos ' lldb launcher.py"
            shutil.copy2(phase.MACOS_LLDB_LAUNCHER, launcher_copy)
            launcher_copy.chmod(0o500)
            base_dir = root / "instance dir ' ☃"
            compiled = subprocess.run(
                ["cc", "-x", "c", "-o", str(program), "-"],
                input=source.encode("utf-8"), capture_output=True, check=False)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            program.chmod(0o500)

            signal_helper = root / "sdb signal injection.py"
            signal_helper.write_text(textwrap.dedent(r'''
                import argparse
                import importlib.util
                import json
                import os
                from pathlib import Path
                import signal
                import sys

                repository = Path(sys.argv[1])
                sys.path.insert(0, str(repository / "unittest/ios_build"))
                import test_macos_lldb_launcher as contract
                sdb_path = repository / ".github/script/seekdb/sdb.py"
                spec = importlib.util.spec_from_file_location("signal_sdb", sdb_path)
                sdb = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sdb)
                base_dir = Path(sys.argv[2])
                binary = Path(sys.argv[3])
                launcher_path = Path(sys.argv[4])
                mode = sys.argv[5]
                signum = int(sys.argv[6])
                evidence = Path(sys.argv[7])

                def capture(process):
                    """Persist the real launcher chain before signal injection."""
                    lldb_pid = contract._wait_for_lldb_session_leader(process.pid)
                    target_pid = contract._wait_for_lldb_target(lldb_pid, binary)
                    evidence.write_text(json.dumps(
                        [process.pid, lldb_pid, target_pid]), encoding="utf-8")

                original_spawn = sdb.spawn_detached
                original_write = sdb.write_launcher_marker

                def injected_spawn(
                        command, working_directory, console,
                        child_signal_mask=None):
                    """Inject while command_start still blocks ownership signals."""
                    process = original_spawn(
                        command, working_directory, console,
                        child_signal_mask=child_signal_mask)
                    if mode == "spawn":
                        capture(process)
                        os.kill(os.getpid(), signum)
                    return process

                def injected_write(working_directory, record):
                    """Inject immediately after the durable marker transaction."""
                    original_write(working_directory, record)
                    if mode == "marker":
                        class Process:
                            pid = record["pid"]
                        capture(Process())
                        os.kill(os.getpid(), signum)

                sdb.spawn_detached = injected_spawn
                sdb.write_launcher_marker = injected_write
                options = argparse.Namespace(
                    base_dir=str(base_dir), binary=str(binary),
                    launcher=str(launcher_path), port=2884, nodaemon=True,
                    parameter=["", "signal value with spaces"])
                raise SystemExit(sdb.command_start(options))
            '''), encoding="utf-8")
            for mode, signum, expected_status in (
                    ("spawn", signal.SIGTERM, 143),
                    ("marker", signal.SIGINT, 130)):
                signal_base = root / "{} signal instance".format(mode)
                signal_evidence = root / "{}-signal-pids.json".format(mode)
                interrupted = subprocess.run([
                    sys.executable, str(signal_helper), str(REPOSITORY_ROOT),
                    str(signal_base), str(program), str(launcher_copy), mode,
                    str(int(signum)), str(signal_evidence),
                ], capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(
                    expected_status, interrupted.returncode,
                    interrupted.stdout + interrupted.stderr)
                signal_pids = json.loads(
                    signal_evidence.read_text(encoding="utf-8"))
                self.assertFalse((
                    signal_base / "run/.sdb-launcher.json").exists())
                for pid in signal_pids:
                    self.assertFalse(
                        _pid_exists(pid),
                        "signal-window pid {} survived".format(pid))

            lock_helper = root / "sdb lifecycle lock helper.py"
            lock_helper.write_text(textwrap.dedent(r'''
                import argparse
                import importlib.util
                import os
                from pathlib import Path
                import sys
                import time

                repository = Path(sys.argv[1])
                sdb_path = repository / ".github/script/seekdb/sdb.py"
                spec = importlib.util.spec_from_file_location("lock_sdb", sdb_path)
                sdb = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sdb)
                base_dir = Path(sys.argv[2])
                binary = Path(sys.argv[3])
                mode = sys.argv[4]
                ready = Path(sys.argv[5])
                release = Path(sys.argv[6])
                with sdb.lifecycle_lock(base_dir):
                    if mode == "preflight":
                        sdb.preflight_launcher_marker(base_dir, binary)
                    else:
                        options = argparse.Namespace(
                            base_dir=str(base_dir), quiet=True,
                            require_match=True)
                        if sdb._command_stop_locked(options, base_dir) != 0:
                            raise SystemExit(3)
                    ready.write_text(str(os.getpid()), encoding="ascii")
                    deadline = time.monotonic() + 20
                    while not release.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    if not release.exists():
                        raise SystemExit(4)
            '''), encoding="utf-8")
            concurrent_base = root / "concurrent instance"
            sdb.prepare_instance_directory(concurrent_base, program)
            concurrent_args = argparse.Namespace(
                base_dir=str(concurrent_base), binary=str(program),
                launcher=str(launcher_copy), port=2885, nodaemon=True,
                parameter=[""])
            stale_command = sdb.build_start_command(
                concurrent_args, concurrent_base)
            stale_record = {
                "version": sdb.LAUNCHER_MARKER_VERSION,
                "pid": 99999999,
                "start_identity": "exited-concurrent-start",
                "process_executable": str(Path(sys.executable).resolve()),
                "launcher_executable": str(launcher_copy),
                "target_binary": str(program),
                "base_dir": str(concurrent_base),
                "argv": stale_command,
            }
            sdb.write_launcher_marker(concurrent_base, stale_record)
            preflight_ready = root / "preflight-ready"
            preflight_release = root / "preflight-release"
            preflight = subprocess.Popen([
                sys.executable, str(lock_helper), str(REPOSITORY_ROOT),
                str(concurrent_base), str(program), "preflight",
                str(preflight_ready), str(preflight_release),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            preflight_deadline = time.monotonic() + 10
            while (not preflight_ready.exists()
                   and time.monotonic() < preflight_deadline):
                if preflight.poll() is not None:
                    output = preflight.communicate()
                    self.fail("preflight helper failed: {}".format(
                        "".join(output)))
                time.sleep(0.02)
            self.assertTrue(preflight_ready.exists())
            first_start = subprocess.Popen([
                sys.executable, str(sdb_script), "start",
                "--binary", str(program), "--launcher", str(launcher_copy),
                "--base-dir", str(concurrent_base), "--port", "2885",
                "--nodaemon", "--parameter", "",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.25)
            self.assertIsNone(first_start.poll())
            preflight_release.write_text("release", encoding="ascii")
            preflight_output = preflight.communicate(timeout=10)
            self.assertEqual(0, preflight.returncode, "".join(preflight_output))
            first_output = first_start.communicate(timeout=15)
            self.assertEqual(0, first_start.returncode, "".join(first_output))
            first_record = json.loads((
                concurrent_base / "run/.sdb-launcher.json").read_text(
                    encoding="utf-8"))
            first_launcher_pid = first_record["pid"]
            first_lldb_pid = _wait_for_lldb_session_leader(first_launcher_pid)
            first_target_pid = _wait_for_lldb_target(first_lldb_pid, program)

            stop_ready = root / "stop-ready"
            stop_release = root / "stop-release"
            stopper = subprocess.Popen([
                sys.executable, str(lock_helper), str(REPOSITORY_ROOT),
                str(concurrent_base), str(program), "stop",
                str(stop_ready), str(stop_release),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stop_deadline = time.monotonic() + 20
            while not stop_ready.exists() and time.monotonic() < stop_deadline:
                if stopper.poll() is not None:
                    output = stopper.communicate()
                    self.fail("stop helper failed: {}".format("".join(output)))
                time.sleep(0.02)
            self.assertTrue(stop_ready.exists())
            second_start = subprocess.Popen([
                sys.executable, str(sdb_script), "start",
                "--binary", str(program), "--launcher", str(launcher_copy),
                "--base-dir", str(concurrent_base), "--port", "2885",
                "--nodaemon", "--parameter", "",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.25)
            self.assertIsNone(second_start.poll())
            stop_release.write_text("release", encoding="ascii")
            stop_output = stopper.communicate(timeout=10)
            self.assertEqual(0, stopper.returncode, "".join(stop_output))
            second_output = second_start.communicate(timeout=15)
            self.assertEqual(0, second_start.returncode, "".join(second_output))
            second_record = json.loads((
                concurrent_base / "run/.sdb-launcher.json").read_text(
                    encoding="utf-8"))
            self.assertNotEqual(first_launcher_pid, second_record["pid"])
            for pid in (first_launcher_pid, first_lldb_pid, first_target_pid):
                self.assertFalse(
                    _pid_exists(pid), "old concurrent pid {} survived".format(pid))
            second_launcher_pid = second_record["pid"]
            second_lldb_pid = _wait_for_lldb_session_leader(second_launcher_pid)
            second_target_pid = _wait_for_lldb_target(second_lldb_pid, program)
            concurrent_destroy = subprocess.run([
                sys.executable, str(sdb_script), "destroy",
                "--base-dir", str(concurrent_base),
            ], capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(
                0, concurrent_destroy.returncode, concurrent_destroy.stderr)
            for pid in (
                    second_launcher_pid, second_lldb_pid, second_target_pid):
                self.assertFalse(
                    _pid_exists(pid), "new concurrent pid {} survived".format(pid))

            failed_base = root / 'failed instance " ☃'
            failed_args = argparse.Namespace(
                base_dir=str(failed_base), binary=str(program),
                launcher=str(launcher_copy), port=2882,
                nodaemon=True,
                parameter=["", 'value with spaces \' " and ☃'])
            original_cleanup = sdb._cleanup_new_launcher
            failed_pids = []

            def capture_and_cleanup(process, command, start_identity=None):
                """Capture the real failed-start chain before normal cleanup."""
                lldb_pid = _wait_for_lldb_session_leader(process.pid)
                target_pid = _wait_for_lldb_target(lldb_pid, program)
                failed_pids.extend((process.pid, lldb_pid, target_pid))
                return original_cleanup(process, command, start_identity)

            malformed_marker = b"{race-invalid-json\n"

            def inject_malformed_marker(working_directory, _record):
                """Create a foreign malformed marker after the real spawn."""
                run_dir = working_directory / "run"
                run_dir.mkdir(exist_ok=True)
                (run_dir / sdb.LAUNCHER_MARKER_NAME).write_bytes(
                    malformed_marker)
                raise OSError("injected marker failure")

            with mock.patch.object(
                    sdb, "write_launcher_marker",
                    side_effect=inject_malformed_marker), \
                    mock.patch.object(
                        sdb, "_cleanup_new_launcher",
                        side_effect=capture_and_cleanup):
                self.assertEqual(1, sdb.command_start(failed_args))
            for pid in failed_pids:
                self.assertFalse(
                    _pid_exists(pid), "failed-start pid {} survived".format(pid))
            self.assertEqual(
                malformed_marker,
                (failed_base / "run" / sdb.LAUNCHER_MARKER_NAME).read_bytes())

            started = subprocess.run([
                sys.executable, str(sdb_script), "start",
                "--binary", str(program),
                "--launcher", str(launcher_copy),
                "--base-dir", str(base_dir), "--nodaemon",
                "--parameter", "", "--parameter",
                'value with spaces \' " and ☃',
            ], capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(0, started.returncode, started.stderr)
            marker_path = base_dir / "run" / ".sdb-launcher.json"
            record = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(str(launcher_copy), record["argv"][1])
            self.assertIn("", record["argv"])
            self.assertIn('value with spaces \' " and ☃', record["argv"])
            launcher_pid = record["pid"]
            lldb_pid = _wait_for_lldb_session_leader(launcher_pid)
            target_pid = _wait_for_lldb_target(lldb_pid, program)
            self.assertEqual(
                target_pid,
                _wait_for_pid_file(base_dir / "target-started.pid"))
            try:
                destroyed = subprocess.run([
                    sys.executable, str(sdb_script), "destroy",
                    "--base-dir", str(base_dir),
                ], capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(0, destroyed.returncode, destroyed.stderr)
                self.assertFalse(base_dir.exists())
                for pid in (launcher_pid, lldb_pid, target_pid):
                    self.assertFalse(_pid_exists(pid), "pid {} survived".format(pid))
            finally:
                for pid in (launcher_pid, lldb_pid, target_pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

            managed_base = root / "managed instance ' ☃"
            managed_start = subprocess.run([
                sys.executable, str(sdb_script), "start",
                "--binary", str(program),
                "--launcher", str(launcher_copy),
                "--base-dir", str(managed_base), "--port", "2883",
                "--nodaemon", "--parameter", "",
            ], capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(0, managed_start.returncode, managed_start.stderr)
            managed_record = json.loads((
                managed_base / "run/.sdb-launcher.json").read_text(
                    encoding="utf-8"))
            managed_launcher_pid = managed_record["pid"]
            managed_lldb_pid = _wait_for_lldb_session_leader(
                managed_launcher_pid)
            managed_target_pid = _wait_for_lldb_target(
                managed_lldb_pid, program)
            self.assertEqual(
                managed_target_pid,
                _wait_for_pid_file(managed_base / "run/seekdb.pid"))
            try:
                managed_destroy = subprocess.run([
                    sys.executable, str(sdb_script), "destroy",
                    "--base-dir", str(managed_base),
                ], capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(
                    0, managed_destroy.returncode, managed_destroy.stderr)
                self.assertFalse(managed_base.exists())
                for pid in (managed_launcher_pid, managed_lldb_pid,
                            managed_target_pid):
                    self.assertFalse(
                        _pid_exists(pid), "managed pid {} survived".format(pid))
            finally:
                for pid in (managed_launcher_pid, managed_lldb_pid,
                            managed_target_pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_default_launcher_timeout_precedes_outer_host_deadline(self):
        """Ensure the launcher owns cleanup before the outer host deadline."""
        self.assertLess(
            launcher.DEFAULT_TIMEOUT_SECONDS, phase.HOST_RUN_TIMEOUT_SECONDS)

    def test_external_signal_before_spawn_maps_status_and_restores_handlers(self):
        """Handle termination before child creation without changing handlers."""
        previous = signal.getsignal(signal.SIGTERM)

        def terminate_before_spawn(_value):
            """Inject termination while the launcher validates its target."""
            launcher._handle_external_signal(signal.SIGTERM, None)

        with mock.patch.object(
                launcher, "validate_binary", side_effect=terminate_before_spawn):
            result = launcher.main(["--binary", "/snapshot/seekdb"])

        self.assertEqual(128 + signal.SIGTERM, result)
        self.assertIs(previous, signal.getsignal(signal.SIGTERM))

    def test_pending_signal_on_initial_unmask_maps_status_and_restores_state(self):
        """Catch a signal delivered by the first post-install mask restore."""
        previous_term = signal.getsignal(signal.SIGTERM)
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, launcher.HANDLED_SIGNALS)

        def install_with_pending_signal():
            """Install real handlers and enqueue TERM before initial unmask."""
            previous = {}
            for signum in launcher.HANDLED_SIGNALS:
                previous[signum] = signal.signal(
                    signum, launcher._handle_external_signal)
            os.kill(os.getpid(), signal.SIGTERM)
            return previous, previous_mask

        try:
            with mock.patch.object(
                    launcher, "_install_signal_handlers",
                    side_effect=install_with_pending_signal):
                result = launcher.main(["--binary", "/snapshot/seekdb"])
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

        self.assertEqual(128 + signal.SIGTERM, result)
        self.assertIs(previous_term, signal.getsignal(signal.SIGTERM))

    def test_signal_during_handler_restore_is_consumed_after_full_restore(self):
        """Restore every handler and mask before reporting teardown TERM or INT."""
        for injected_signal in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(injected_signal=injected_signal):
                previous, original_mask = launcher._install_signal_handlers()
                signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
                real_signal = signal.signal
                injected = False

                def restore_and_inject(signum, handler):
                    """Enqueue one signal while teardown holds the signal mask."""
                    nonlocal injected
                    result = real_signal(signum, handler)
                    if not injected:
                        injected = True
                        os.kill(os.getpid(), injected_signal)
                    return result

                try:
                    with mock.patch.object(
                            launcher.signal, "signal",
                            side_effect=restore_and_inject):
                        captured = launcher._restore_signal_handlers(
                            previous, original_mask)
                finally:
                    for signum, handler in previous.items():
                        real_signal(signum, handler)
                    signal.pthread_sigmask(
                        signal.SIG_SETMASK, original_mask)

                self.assertEqual(injected_signal, captured)
                self.assertEqual(
                    128 + injected_signal, 128 + captured)
                for signum, handler in previous.items():
                    self.assertIs(handler, signal.getsignal(signum))
                current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                self.assertEqual(original_mask, current_mask)

    def test_main_maps_a_teardown_signal_without_traceback(self):
        """Return shell signal status when transactional restore reports INT."""
        with mock.patch.object(
                launcher, "_install_signal_handlers",
                return_value=({}, set())), \
                mock.patch.object(
                    launcher, "validate_binary",
                    side_effect=launcher.LauncherError("fixed")), \
                mock.patch.object(
                    launcher, "_restore_signal_handlers",
                    return_value=signal.SIGINT):
            result = launcher.main(["--binary", "/snapshot/seekdb"])

        self.assertEqual(128 + signal.SIGINT, result)

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
                mock.patch.object(
                    launcher, "_process_table", return_value={}), \
                mock.patch.object(launcher.os, "killpg") as killpg:
            result = launcher.launch(
                Path("/snapshot/seekdb"), (), timeout_seconds=1)

        self.assertEqual(124, result)
        self.assertEqual(
            [mock.call(4321, signal.SIGSTOP),
             mock.call(4321, signal.SIGCONT),
             mock.call(4321, signal.SIGTERM),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)
        popen_options = popen.call_args.kwargs
        self.assertEqual(subprocess.DEVNULL, popen_options["stdin"])
        self.assertEqual(4, len(popen_options["pass_fds"]))

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
                mock.patch.object(
                    launcher, "_process_table", return_value={}), \
                mock.patch.object(launcher.os, "killpg") as killpg:
            with self.assertRaises(KeyboardInterrupt):
                launcher.launch(
                    Path("/snapshot/seekdb"), (), timeout_seconds=1)

        self.assertEqual(
            [mock.call(4321, signal.SIGSTOP),
             mock.call(4321, signal.SIGCONT),
             mock.call(4321, signal.SIGTERM),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)

    def test_cleanup_kills_the_group_after_the_lldb_leader_exits(self):
        """Kill remaining descendants even when the LLDB leader exits on TERM."""
        process = mock.Mock(pid=4321)
        process.wait.return_value = 0

        records = {
            5000: launcher._ProcessRecord(
                5000, 4321, 5000, "S", "/debugger")}
        with mock.patch.object(
                launcher, "_process_table", return_value=records), \
                mock.patch.object(launcher.os, "kill") as kill_process, \
                mock.patch.object(launcher.os, "killpg") as killpg:
            launcher._terminate_group(process)

        self.assertEqual(
            [mock.call(4321, signal.SIGSTOP),
             mock.call(4321, signal.SIGCONT),
             mock.call(5000, signal.SIGTERM),
             mock.call(4321, signal.SIGTERM),
             mock.call(5000, signal.SIGKILL),
             mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list)
        self.assertEqual(
            [mock.call(5000, signal.SIGTERM),
             mock.call(5000, signal.SIGKILL)],
            kill_process.call_args_list)
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
                _close_process_pipes(tty_process)
                os.close(master_fd)
                if slave_fd >= 0:
                    os.close(slave_fd)
            self.assertEqual(0, tty_process.returncode, tty_stderr)
            self.assertEqual(b"tty input\n", tty_stdout)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("cc"),
        "requires macOS, clang, and LLDB")
    def test_external_term_and_interrupt_reap_lldb_and_sleeping_target(self):
        """Map external signals only after the LLDB session is fully reaped."""
        source = r'''
#include <stdio.h>
#include <unistd.h>
int main(int argc, char **argv) {
  FILE *marker = fopen(argv[1], "w");
  if (marker == NULL) return 2;
  fprintf(marker, "%d\n", getpid());
  if (fclose(marker) != 0) return 3;
  sleep(300);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            program = root / "sleeper"
            compiled = subprocess.run(
                ["cc", "-x", "c", "-o", str(program), "-"],
                input=source.encode("utf-8"), capture_output=True,
                check=False)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            program.chmod(0o500)
            for sent_signal in (signal.SIGTERM, signal.SIGINT):
                with self.subTest(sent_signal=sent_signal):
                    marker = root / "target-{}.pid".format(sent_signal)
                    process = subprocess.Popen([
                        sys.executable, str(phase.MACOS_LLDB_LAUNCHER),
                        "--binary", str(program), "--timeout", "120", "--",
                        str(marker),
                    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
                    target_pid = _wait_for_pid_file(marker)
                    try:
                        os.kill(process.pid, sent_signal)
                        stdout, stderr = process.communicate(timeout=15)
                        self.assertEqual(
                            128 + sent_signal, process.returncode,
                            stdout + stderr)
                        deadline = time.monotonic() + 5
                        while (_pid_exists(target_pid)
                               and time.monotonic() < deadline):
                            time.sleep(0.05)
                        self.assertFalse(_pid_exists(target_pid))
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        _close_process_pipes(process)
                        try:
                            os.kill(target_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

            timeout_marker = root / "target-timeout.pid"
            timed_out = subprocess.run([
                sys.executable, str(phase.MACOS_LLDB_LAUNCHER),
                "--binary", str(program), "--timeout", "3", "--",
                str(timeout_marker),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
               stderr=subprocess.PIPE, timeout=10, check=False)
            self.assertEqual(124, timed_out.returncode, timed_out.stderr)
            timed_out_target = _wait_for_pid_file(timeout_marker)
            deadline = time.monotonic() + 5
            while (_pid_exists(timed_out_target)
                   and time.monotonic() < deadline):
                time.sleep(0.05)
            self.assertFalse(_pid_exists(timed_out_target))

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("cc"),
        "requires macOS, clang, and LLDB")
    def test_external_timeout_cleanup_before_target_creates_managed_pid(self):
        """Reap the LLDB group when outer cleanup wins before target startup."""
        source = r'''
#include <stdio.h>
#include <unistd.h>
int main(int argc, char **argv) {
  FILE *started = fopen(argv[2], "w");
  if (started == NULL) return 2;
  fprintf(started, "%d\n", getpid());
  if (fclose(started) != 0) return 3;
  sleep(20);
  FILE *marker = fopen(argv[1], "w");
  if (marker == NULL) return 4;
  fprintf(marker, "%d\n", getpid());
  fclose(marker);
  sleep(300);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            program = root / "delayed-sleeper"
            marker = root / "managed.pid"
            started_marker = root / "target-started.pid"
            compiled = subprocess.run(
                ["cc", "-x", "c", "-o", str(program), "-"],
                input=source.encode("utf-8"), capture_output=True,
                check=False)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            program.chmod(0o500)
            process = subprocess.Popen([
                sys.executable, str(phase.MACOS_LLDB_LAUNCHER),
                "--binary", str(program), "--timeout", "120", "--",
                str(marker), str(started_marker),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
               stderr=subprocess.PIPE)
            lldb_pid = _wait_for_lldb_session_leader(process.pid)
            target_pid = _wait_for_lldb_target(lldb_pid, program)
            self.assertEqual(target_pid, _wait_for_pid_file(started_marker))
            captured_records = launcher._process_table()
            self.assertIn(
                target_pid,
                launcher._descendant_ids(lldb_pid, captured_records))
            try:
                self.assertFalse(marker.exists())
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(143, process.returncode, stdout + stderr)
                self.assertFalse(marker.exists())
                deadline = time.monotonic() + 5
                while (_pid_exists(target_pid)
                       and time.monotonic() < deadline):
                    time.sleep(0.05)
                self.assertFalse(_pid_exists(target_pid))
                with self.assertRaises(ProcessLookupError):
                    os.killpg(lldb_pid, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                _close_process_pipes(process)
                try:
                    os.killpg(lldb_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

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
