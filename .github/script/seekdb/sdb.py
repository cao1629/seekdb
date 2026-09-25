#!/usr/bin/env python3
"""Start, wait for, stop, and destroy a local seekdb instance."""

from __future__ import print_function

import argparse
import ctypes
import errno
import json
import os
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_PORT = 2881
DEFAULT_READY_TIMEOUT = 180.0
DEFAULT_READY_INTERVAL = 1.0
CLIENT_ATTEMPT_TIMEOUT = 5.0
STOP_TIMEOUT = 20.0
KILL_TIMEOUT = 5.0
PROCESS_QUERY_TIMEOUT = 5.0
LAUNCHER_CLEANUP_BUDGET = 1.25
INSTANCE_MARKER_NAME = ".sdb-instance"
INSTANCE_MARKER_HEADER = "seekdb-instance-v1"
LAUNCHER_MARKER_NAME = ".sdb-launcher.json"
LAUNCHER_MARKER_VERSION = 1
LAUNCHER_MARKER_MAX_BYTES = 64 * 1024
MACOS_CTL_KERN = 1
MACOS_KERN_ARGMAX = 8
MACOS_KERN_PROCARGS2 = 49
MAX_PROCESS_ARGUMENT_BYTES = 4 * 1024 * 1024
MAX_PROCESS_ARGUMENT_COUNT = 65536
START_HANDLED_SIGNALS = tuple(
    item for item in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    if item is not None)
_START_PENDING_SIGNAL = 0


class _StartTermination(BaseException):
    """Interrupt launcher ownership setup after an external signal."""

    def __init__(self, signum):
        """Record the signal whose conventional shell status is required."""
        super().__init__(signum)
        self.signum = signum


def _error(message):
    print("[sdb][ERROR] {}".format(message), file=sys.stderr)


def _warning(message):
    print("[sdb][WARN] {}".format(message), file=sys.stderr)


def _handle_start_signal(signum, _frame):
    """Record one signal and interrupt the launcher ownership transaction."""
    global _START_PENDING_SIGNAL
    if _START_PENDING_SIGNAL:
        return
    _START_PENDING_SIGNAL = signum
    raise _StartTermination(signum)


def _install_start_signal_handlers():
    """Install transaction handlers while their signals remain blocked."""
    global _START_PENDING_SIGNAL
    _START_PENDING_SIGNAL = 0
    original_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK, START_HANDLED_SIGNALS)
    previous = {}
    try:
        for signum in START_HANDLED_SIGNALS:
            previous[signum] = signal.signal(signum, _handle_start_signal)
    except BaseException:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        raise
    return previous, original_mask


def _block_and_collect_start_signals():
    """Block transaction signals and consume one pending termination signal."""
    global _START_PENDING_SIGNAL
    signal.pthread_sigmask(signal.SIG_BLOCK, START_HANDLED_SIGNALS)
    termination_signal = _START_PENDING_SIGNAL
    pending = set(signal.sigpending()).intersection(START_HANDLED_SIGNALS)
    for signum in sorted(pending):
        signal.sigwait({signum})
        if not termination_signal:
            termination_signal = signum
    return termination_signal


def _restore_start_signal_handlers(previous):
    """Restore every transaction handler while signals remain blocked."""
    global _START_PENDING_SIGNAL
    first_error = None
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is None:
        _START_PENDING_SIGNAL = 0
        return
    raise first_error


def _base_dir(value):
    path = Path(os.path.abspath(os.path.expanduser(value)))
    return path.parent.resolve() / path.name


def _expand_command(value):
    return os.path.expanduser(value)


def _executable_path(value):
    return Path(os.path.abspath(_expand_command(value))).resolve()


def _instance_marker(base_dir):
    return base_dir / INSTANCE_MARKER_NAME


def validate_base_dir(base_dir):
    if base_dir.is_symlink():
        raise ValueError("base-dir must not be a symbolic link")

    resolved = base_dir.resolve()
    filesystem_root = Path(resolved.anchor)
    home_dir = Path.home().resolve()
    current_dir = Path.cwd().resolve()
    if resolved == filesystem_root:
        raise ValueError("filesystem root is not allowed as base-dir")
    if resolved == home_dir or resolved in home_dir.parents:
        raise ValueError("HOME or one of its parents is not allowed as base-dir")
    if resolved == current_dir or resolved in current_dir.parents:
        raise ValueError(
            "current directory or one of its parents is not allowed as base-dir"
        )


def read_instance_binary(base_dir):
    marker = _instance_marker(base_dir)
    if marker.is_symlink():
        raise ValueError("instance marker must not be a symbolic link")
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise ValueError("base-dir is not managed by sdb (missing {})".format(marker))
    if len(lines) != 2 or lines[0] != INSTANCE_MARKER_HEADER or not lines[1]:
        raise ValueError("invalid instance marker: {}".format(marker))
    binary = Path(lines[1])
    if not binary.is_absolute():
        raise ValueError("invalid instance marker: {}".format(marker))
    return binary.resolve()


def prepare_instance_directory(base_dir, binary):
    validate_base_dir(base_dir)
    binary = binary.resolve()
    marker = _instance_marker(base_dir)
    write_marker = True
    if base_dir.exists():
        if not base_dir.is_dir():
            raise ValueError("base-dir is not a directory: {}".format(base_dir))
        if marker.is_symlink():
            raise ValueError("instance marker must not be a symbolic link")
        if marker.exists():
            existing_binary = read_instance_binary(base_dir)
            if existing_binary != binary:
                raise ValueError(
                    "base-dir is managed by {}, not {}".format(
                        existing_binary, binary
                    )
                )
            write_marker = False
        elif any(base_dir.iterdir()):
            raise ValueError(
                "refusing to use non-empty base-dir without {}".format(
                    INSTANCE_MARKER_NAME
                )
            )
    else:
        base_dir.mkdir(parents=True)

    if write_marker:
        marker.write_text(
            "{}\n{}\n".format(INSTANCE_MARKER_HEADER, binary), encoding="utf-8"
        )


def launch_prefix(launcher, binary, launcher_timeout=None):
    """Return a structured optional launcher prefix for one real executable."""
    binary = str(_executable_path(binary))
    if not launcher:
        return [binary]
    command = [
        sys.executable, str(_executable_path(launcher)), "--binary", binary]
    if launcher_timeout is not None:
        command.extend(("--timeout", str(launcher_timeout)))
    command.append("--")
    return command


def build_start_command(args, base_dir):
    command = launch_prefix(getattr(args, "launcher", None), args.binary) + [
        "--base-dir={}".format(base_dir),
        "--port={}".format(args.port),
    ]
    if args.nodaemon:
        command.append("--nodaemon")
    for parameter in args.parameter:
        command.extend(("--parameter", parameter))
    return command


def spawn_detached(command, base_dir, console, child_signal_mask=None):
    """Spawn a detached process with an explicit inherited signal mask."""
    if os.name == "nt":
        raise RuntimeError("Windows is not supported yet")

    def restore_child_signal_mask():
        """Restore the pre-transaction mask immediately before child exec."""
        if child_signal_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, child_signal_mask)

    options = {
        "cwd": str(base_dir),
        "stdout": console,
        "stderr": subprocess.STDOUT,
        "start_new_session": True,
    }
    if child_signal_mask is not None:
        options["preexec_fn"] = restore_child_signal_mask
    return subprocess.Popen(command, **options)


def read_process_start_identity(pid):
    """Return a stable operating-system start identity for one live process."""
    if sys.platform.startswith("linux"):
        try:
            process_stat = Path("/proc/{}/stat".format(pid)).read_text(
                encoding="ascii")
        except FileNotFoundError:
            return None
        command_end = process_stat.rfind(") ")
        fields = process_stat[command_end + 2:].split() if command_end >= 0 else []
        if len(fields) <= 19:
            raise RuntimeError("invalid process stat for pid={}".format(pid))
        return fields[19]

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-ww", "-o", "lstart="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PROCESS_QUERY_TIMEOUT,
            universal_newlines=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("ps is required to inspect the launcher process")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return " ".join(result.stdout.split())


def _open_run_directory(base_dir, create=False):
    """Open the instance run directory without following its final component."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    base_fd = os.open(str(base_dir), directory_flags | nofollow)
    try:
        if create:
            try:
                os.mkdir("run", mode=0o700, dir_fd=base_fd)
            except FileExistsError:
                pass
        return os.open("run", directory_flags | nofollow, dir_fd=base_fd)
    finally:
        os.close(base_fd)


def _launcher_record(process, command, base_dir, expected_binary):
    """Build the exact identity record for a newly detached launcher."""
    deadline = time.monotonic() + PROCESS_QUERY_TIMEOUT
    arguments = None
    previous_arguments = None
    start_identity = None
    while time.monotonic() < deadline:
        process_executable, arguments = read_process_identity(process.pid)
        start_identity = read_process_start_identity(process.pid)
        if arguments and arguments[1:] == list(command)[1:] and start_identity \
                and arguments == previous_arguments:
            break
        previous_arguments = arguments
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if not arguments or arguments[1:] != list(command)[1:] or not start_identity:
        raise RuntimeError("failed to validate the newly started launcher")
    return {
        "version": LAUNCHER_MARKER_VERSION,
        "pid": process.pid,
        "start_identity": start_identity,
        "process_executable": str(process_executable),
        "launcher_executable": str(_executable_path(command[1])),
        "target_binary": str(expected_binary.resolve()),
        "base_dir": str(base_dir),
        "argv": arguments,
    }


def _validate_launcher_record(record, base_dir, expected_binary):
    """Validate marker structure and its binding to one managed instance."""
    required = {
        "version", "pid", "start_identity", "process_executable",
        "launcher_executable", "target_binary", "base_dir", "argv",
    }
    if set(record) != required or record.get("version") != LAUNCHER_MARKER_VERSION:
        raise RuntimeError("invalid launcher marker schema")
    pid = record.get("pid")
    argv = record.get("argv")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(argv, list) \
            or not argv or not all(isinstance(value, str) for value in argv):
        raise RuntimeError("invalid launcher marker identity")
    scalar_keys = (
        "start_identity", "process_executable", "launcher_executable",
        "target_binary", "base_dir",
    )
    if not all(isinstance(record.get(key), str) and record[key]
               for key in scalar_keys):
        raise RuntimeError("invalid launcher marker identity")
    if record["base_dir"] != str(base_dir) \
            or record["target_binary"] != str(expected_binary.resolve()):
        raise RuntimeError("launcher marker does not match this instance")
    expected_base_argument = "--base-dir={}".format(base_dir)
    if len(argv) < 6 or not Path(record["process_executable"]).is_absolute() \
            or _executable_path(argv[1]) != Path(record["launcher_executable"]) \
            or argv[2:4] != ["--binary", record["target_binary"]] \
            or expected_base_argument not in argv:
        raise RuntimeError("launcher marker command is invalid")
    return record


def write_launcher_marker(base_dir, record):
    """Atomically persist one launcher identity through an anchored directory."""
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(payload) > LAUNCHER_MARKER_MAX_BYTES:
        raise RuntimeError("launcher marker is too large")
    run_fd = _open_run_directory(base_dir, create=True)
    temporary_name = ".sdb-launcher.{}.{}.tmp".format(
        os.getpid(), secrets.token_hex(8))
    descriptor = None
    try:
        try:
            existing = os.stat(
                LAUNCHER_MARKER_NAME, dir_fd=run_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            raise RuntimeError("launcher marker already exists")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=run_fd,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(
            temporary_name, LAUNCHER_MARKER_NAME,
            src_dir_fd=run_fd, dst_dir_fd=run_fd,
            follow_symlinks=False)
        os.unlink(temporary_name, dir_fd=run_fd)
        os.fsync(run_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=run_fd)
        except FileNotFoundError:
            pass
        os.close(run_fd)


def read_launcher_marker(base_dir, expected_binary):
    """Read a bounded regular launcher marker without following links."""
    try:
        run_fd = _open_run_directory(base_dir)
    except FileNotFoundError:
        return None
    descriptor = None
    try:
        try:
            descriptor = os.open(
                LAUNCHER_MARKER_NAME,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=run_fd,
            )
        except FileNotFoundError:
            return None
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) \
                or metadata.st_size > LAUNCHER_MARKER_MAX_BYTES:
            raise RuntimeError("launcher marker is not a bounded regular file")
        payload = os.read(descriptor, LAUNCHER_MARKER_MAX_BYTES + 1)
        if len(payload) != metadata.st_size:
            raise RuntimeError("launcher marker changed while reading")
        try:
            record = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise RuntimeError("launcher marker is invalid")
        if not isinstance(record, dict):
            raise RuntimeError("launcher marker is invalid")
        return _validate_launcher_record(record, base_dir, expected_binary)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(run_fd)


def remove_launcher_marker(base_dir):
    """Remove a regular launcher marker through its anchored run directory."""
    try:
        run_fd = _open_run_directory(base_dir)
    except FileNotFoundError:
        return
    try:
        try:
            metadata = os.stat(
                LAUNCHER_MARKER_NAME, dir_fd=run_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("launcher marker is not a regular file")
        os.unlink(LAUNCHER_MARKER_NAME, dir_fd=run_fd)
        os.fsync(run_fd)
    finally:
        os.close(run_fd)


def _launcher_process_matches(record):
    """Return whether a live process still has the recorded launcher identity."""
    process_executable, arguments = read_process_identity(record["pid"])
    start_identity = read_process_start_identity(record["pid"])
    return process_executable == Path(record["process_executable"]) \
        and arguments == record["argv"] \
        and start_identity == record["start_identity"]


def terminate_launcher_record(record):
    """Terminate one revalidated launcher and wait for its layered cleanup."""
    pid = record["pid"]
    if not process_exists(pid):
        return
    if not _launcher_process_matches(record):
        raise RuntimeError("launcher pid={} identity does not match marker".format(pid))
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if not wait_process_exit(pid, STOP_TIMEOUT):
        raise RuntimeError("launcher pid={} did not finish cleanup".format(pid))


def cleanup_launcher(base_dir, expected_binary):
    """Clean a persisted launcher or safely discard its exited marker."""
    record = read_launcher_marker(base_dir, expected_binary)
    if record is None:
        return
    terminate_launcher_record(record)
    remove_launcher_marker(base_dir)


def _cleanup_new_launcher(process, _command, start_identity=None):
    """Stop a just-spawned launcher when durable marker creation fails."""
    if process.poll() is not None:
        return
    current_start = read_process_start_identity(process.pid)
    if start_identity is not None and current_start != start_identity:
        raise RuntimeError("new launcher identity changed before cleanup")
    process.terminate()
    try:
        process.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("new launcher did not finish cleanup")


def _abort_launcher_start(process, command, base_dir, expected_binary, record):
    """Roll back either in-memory or durable ownership of one new launcher."""
    persisted = None
    if record is not None:
        persisted = read_launcher_marker(base_dir, expected_binary)
    if persisted is not None:
        if persisted != record:
            _cleanup_new_launcher(
                process, command, record.get("start_identity"))
            raise RuntimeError("launcher marker is owned by a different start")
        terminate_launcher_record(persisted)
        remove_launcher_marker(base_dir)
        return
    _cleanup_new_launcher(
        process, command,
        record.get("start_identity") if record else None)


def _command_start_with_launcher(args, base_dir, log_dir, command):
    """Start one launcher inside an interrupt-safe ownership transaction."""
    previous_handlers, original_mask = _install_start_signal_handlers()
    process = None
    record = None
    caught_error = None
    termination_signal = 0
    completed = False
    expected_binary = _executable_path(args.binary)
    try:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
            prepare_instance_directory(base_dir, expected_binary)
            log_dir.mkdir(parents=True, exist_ok=True)
            signal.pthread_sigmask(signal.SIG_BLOCK, START_HANDLED_SIGNALS)
            try:
                with (log_dir / "console.log").open("ab") as console:
                    process = spawn_detached(
                        command, base_dir, console,
                        child_signal_mask=original_mask)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
            record = _launcher_record(
                process, command, base_dir, expected_binary)
            write_launcher_marker(base_dir, record)
            completed = True
        except _StartTermination as exc:
            termination_signal = exc.signum
        except BaseException as exc:
            caught_error = exc
    finally:
        cleanup_error = None
        ownership_aborted = False
        try:
            pending_signal = _block_and_collect_start_signals()
            if not termination_signal:
                termination_signal = pending_signal
            if process is not None \
                    and (termination_signal or caught_error or not completed):
                _abort_launcher_start(
                    process, command, base_dir, expected_binary, record)
                ownership_aborted = True
        except BaseException as exc:
            cleanup_error = exc
            if process is not None and not ownership_aborted:
                try:
                    signal.pthread_sigmask(
                        signal.SIG_BLOCK, START_HANDLED_SIGNALS)
                    _abort_launcher_start(
                        process, command, base_dir, expected_binary, record)
                    ownership_aborted = True
                except BaseException as rollback_exc:
                    cleanup_error = rollback_exc
        restore_error = None
        try:
            _restore_start_signal_handlers(previous_handlers)
        except BaseException as exc:
            restore_error = exc
            if process is not None and not ownership_aborted:
                try:
                    _abort_launcher_start(
                        process, command, base_dir, expected_binary, record)
                    ownership_aborted = True
                except BaseException as rollback_exc:
                    cleanup_error = rollback_exc
            try:
                _restore_start_signal_handlers(previous_handlers)
            except BaseException as retry_exc:
                restore_error = retry_exc
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        if cleanup_error is not None:
            raise cleanup_error
        if restore_error is not None:
            raise restore_error

    if termination_signal:
        return 128 + termination_signal
    if caught_error is not None:
        if isinstance(caught_error, (OSError, RuntimeError, ValueError)):
            _error("failed to start seekdb: {}".format(caught_error))
            return 1
        raise caught_error
    print("started pid={}".format(process.pid))
    return 0


def command_start(args):
    base_dir = _base_dir(args.base_dir)
    log_dir = base_dir / "log"
    command = build_start_command(args, base_dir)

    if getattr(args, "launcher", None):
        return _command_start_with_launcher(args, base_dir, log_dir, command)

    try:
        prepare_instance_directory(base_dir, _executable_path(args.binary))
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "console.log").open("ab") as console:
            process = spawn_detached(command, base_dir, console)
    except (OSError, RuntimeError, ValueError) as exc:
        _error("failed to start seekdb: {}".format(exc))
        return 1

    print("started pid={}".format(process.pid))
    return 0


def build_ready_command(args, launcher_timeout=None):
    return launch_prefix(
        getattr(args, "launcher", None), args.client,
        launcher_timeout=launcher_timeout) + [
        "-h",
        args.host,
        "-P",
        str(args.port),
        "-u{}".format(args.user),
        "-A",
        "-N",
        "-s",
        "-e",
        "select 1",
    ]


def command_wait_ready(args):
    base_dir = _base_dir(args.base_dir)
    deadline = time.monotonic() + args.timeout

    try:
        expected_binary = read_instance_binary(base_dir)
    except (OSError, ValueError) as exc:
        _error("unsafe base-dir {}: {}".format(base_dir, exc))
        return 1

    while time.monotonic() < deadline:
        try:
            managed_pid = inspect_instance_process(base_dir, expected_binary)
        except (OSError, RuntimeError) as exc:
            _error("managed seekdb process is unavailable: {}".format(exc))
            return 1

        remaining = deadline - time.monotonic()
        attempt_timeout = min(CLIENT_ATTEMPT_TIMEOUT, max(remaining, 0.001))
        launcher_timeout = None
        if args.launcher:
            if attempt_timeout <= LAUNCHER_CLEANUP_BUDGET:
                time.sleep(min(0.05, max(remaining, 0)))
                continue
            launcher_timeout = attempt_timeout - LAUNCHER_CLEANUP_BUDGET
        command = build_ready_command(args, launcher_timeout)
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=attempt_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result = None
        except OSError as exc:
            _error("failed to run {}: {}".format(args.client, exc))
            return 1

        if result is not None and result.returncode == 0:
            if managed_pid is None:
                try:
                    managed_pid = inspect_instance_process(base_dir, expected_binary)
                except (OSError, RuntimeError) as exc:
                    _error("managed seekdb process is unavailable: {}".format(exc))
                    return 1
            if managed_pid is None:
                _error(
                    "endpoint {}:{} responded without a managed seekdb process".format(
                        args.host, args.port
                    )
                )
                return 1
            print("ready")
            return 0

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(args.interval, remaining))

    _error(
        "seekdb is not ready at {}:{} within {} seconds".format(
            args.host, args.port, args.timeout
        )
    )
    return 1


def process_exists(pid):
    if os.name != "nt":
        try:
            waited_pid, _ = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                return False
        except ChildProcessError:
            pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_process_exit(pid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.2)
    return not process_exists(pid)


def _parse_macos_procargs(payload):
    """Parse one bounded KERN_PROCARGS2 payload without losing argv bytes."""
    if len(payload) > MAX_PROCESS_ARGUMENT_BYTES:
        raise RuntimeError("process argument payload is too large")
    integer_size = ctypes.sizeof(ctypes.c_int)
    if len(payload) < integer_size:
        raise RuntimeError("process arguments are truncated")
    argc = ctypes.c_int.from_buffer_copy(payload[:integer_size]).value
    if argc <= 0 or argc > MAX_PROCESS_ARGUMENT_COUNT:
        raise RuntimeError("process argument count is invalid")

    cursor = integer_size
    executable_end = payload.find(b"\0", cursor)
    if executable_end <= cursor:
        raise RuntimeError("process executable path is invalid")
    executable = os.fsdecode(payload[cursor:executable_end])
    cursor = executable_end + 1
    while cursor < len(payload) and payload[cursor] == 0:
        cursor += 1
    if cursor >= len(payload):
        raise RuntimeError("process argv is missing")

    arguments = []
    for _index in range(argc):
        argument_end = payload.find(b"\0", cursor)
        if argument_end < 0:
            raise RuntimeError("process argv is truncated")
        arguments.append(os.fsdecode(payload[cursor:argument_end]))
        cursor = argument_end + 1
    if not arguments or not arguments[0]:
        raise RuntimeError("process argv[0] is invalid")
    if not Path(executable).is_absolute():
        raise RuntimeError("process executable path is not absolute")
    return Path(executable), arguments


def _macos_sysctl(mib, output, output_size):
    """Invoke sysctl with fixed integer MIB values and preserve errno."""
    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p, ctypes.c_size_t,
    ]
    sysctl.restype = ctypes.c_int
    mib_values = (ctypes.c_int * len(mib))(*mib)
    ctypes.set_errno(0)
    result = sysctl(
        mib_values, len(mib), output, ctypes.byref(output_size), None, 0)
    return result, ctypes.get_errno(), output_size.value


def _read_macos_process_identity(pid):
    """Read executable and argv through the lossless KERN_PROCARGS2 interface."""
    argument_limit = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argument_limit))
    result, error_number, returned_size = _macos_sysctl(
        (MACOS_CTL_KERN, MACOS_KERN_ARGMAX),
        ctypes.byref(argument_limit), size)
    if result != 0 or returned_size != ctypes.sizeof(argument_limit):
        raise RuntimeError(
            "failed to query the process argument limit: errno={}".format(
                error_number))
    if argument_limit.value <= 0 \
            or argument_limit.value > MAX_PROCESS_ARGUMENT_BYTES:
        raise RuntimeError("process argument limit is unsafe")

    buffer = ctypes.create_string_buffer(argument_limit.value)
    size = ctypes.c_size_t(argument_limit.value)
    result, error_number, returned_size = _macos_sysctl(
        (MACOS_CTL_KERN, MACOS_KERN_PROCARGS2, pid), buffer, size)
    if result != 0:
        if error_number == errno.ESRCH \
                or (error_number == errno.EINVAL and not process_exists(pid)):
            return None
        raise RuntimeError(
            "failed to inspect process {} arguments: errno={}".format(
                pid, error_number))
    if returned_size <= 0 or returned_size > argument_limit.value:
        raise RuntimeError("process argument payload size is invalid")
    return _parse_macos_procargs(buffer.raw[:returned_size])


def read_process_arguments(pid):
    if os.name == "nt":
        raise RuntimeError("Windows is not supported yet")

    if sys.platform.startswith("linux"):
        try:
            command_line = Path("/proc/{}/cmdline".format(pid)).read_bytes()
        except FileNotFoundError:
            return None
        arguments = command_line.split(b"\0")
        if arguments and arguments[-1] == b"":
            arguments.pop()
        return [os.fsdecode(argument) for argument in arguments]

    if sys.platform == "darwin":
        identity = _read_macos_process_identity(pid)
        return None if identity is None else identity[1]
    raise RuntimeError("process argument inspection is unsupported")


def read_process_identity(pid):
    """Return the executable path and lossless argv for one live process."""
    if sys.platform == "darwin":
        identity = _read_macos_process_identity(pid)
        if identity is None:
            return None, None
        executable, arguments = identity
        return executable.resolve(), arguments

    arguments = read_process_arguments(pid)
    if not arguments:
        return None, arguments
    if sys.platform.startswith("linux"):
        try:
            executable = Path(os.readlink("/proc/{}/exe".format(pid))).resolve()
        except FileNotFoundError:
            return None, None
        return executable, arguments
    raise RuntimeError("process identity inspection is unsupported")


def process_matches_instance(pid, base_dir, expected_binary):
    executable, arguments = read_process_identity(pid)
    if not arguments:
        return False

    expected_base_dir = "--base-dir={}".format(base_dir)
    return executable == expected_binary.resolve() and expected_base_dir in arguments


def inspect_instance_process(base_dir, expected_binary):
    pid_file = base_dir / "run" / "seekdb.pid"
    try:
        pid_text = pid_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("failed to read {}: {}".format(pid_file, exc))

    try:
        pid = int(pid_text)
        if pid <= 0:
            raise ValueError
    except ValueError:
        raise RuntimeError("invalid pid in {}: {!r}".format(pid_file, pid_text))

    if not process_exists(pid):
        raise RuntimeError("seekdb pid={} is not running".format(pid))
    if not process_matches_instance(pid, base_dir, expected_binary):
        raise RuntimeError("pid={} does not match this seekdb instance".format(pid))
    return pid


def remove_pid_file(pid_file):
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def terminate_pid(pid, base_dir, expected_binary):
    if os.name == "nt":
        raise RuntimeError("Windows is not supported yet")

    if not process_exists(pid):
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if wait_process_exit(pid, STOP_TIMEOUT):
        return

    try:
        matches_instance = process_matches_instance(pid, base_dir, expected_binary)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            "failed to revalidate process {} before SIGKILL: {}".format(pid, exc)
        )
    if not matches_instance:
        _warning(
            "not sending SIGKILL to pid={}: process no longer matches this instance".format(
                pid
            )
        )
        return

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not wait_process_exit(pid, KILL_TIMEOUT):
        raise RuntimeError("process {} did not exit".format(pid))


def command_stop(args):
    base_dir = _base_dir(args.base_dir)
    pid_file = base_dir / "run" / "seekdb.pid"

    try:
        expected_binary = read_instance_binary(base_dir)
    except (OSError, ValueError) as exc:
        _error("unsafe base-dir {}: {}".format(base_dir, exc))
        return 1

    try:
        pid_text = pid_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        try:
            cleanup_launcher(base_dir, expected_binary)
        except (OSError, RuntimeError, ValueError) as exc:
            _error("failed to stop seekdb launcher: {}".format(exc))
            return 1
        if not getattr(args, "quiet", False):
            print("stopped")
        return 0
    except OSError as exc:
        _error("failed to read {}: {}".format(pid_file, exc))
        return 1

    try:
        pid = int(pid_text)
        if pid <= 0:
            raise ValueError
    except ValueError:
        _warning("removing invalid pid file {}: {!r}".format(pid_file, pid_text))
        remove_pid_file(pid_file)
        try:
            cleanup_launcher(base_dir, expected_binary)
        except (OSError, RuntimeError, ValueError) as exc:
            _error("failed to stop seekdb launcher: {}".format(exc))
            return 1
        if not getattr(args, "quiet", False):
            print("stopped")
        return 0

    if not process_exists(pid):
        remove_pid_file(pid_file)
        try:
            cleanup_launcher(base_dir, expected_binary)
        except (OSError, RuntimeError, ValueError) as exc:
            _error("failed to stop seekdb launcher: {}".format(exc))
            return 1
        if not getattr(args, "quiet", False):
            print("stopped")
        return 0

    try:
        matches_instance = process_matches_instance(pid, base_dir, expected_binary)
    except (OSError, RuntimeError) as exc:
        _error("failed to inspect seekdb pid={}: {}".format(pid, exc))
        return 1
    if not matches_instance:
        if getattr(args, "require_match", False):
            _error(
                "refusing to stop live pid={}: process does not match this instance".format(
                    pid
                )
            )
            return 1
        _warning(
            "ignoring stale pid {} from {}: process does not match this instance".format(
                pid, pid_file
            )
        )
        remove_pid_file(pid_file)
        try:
            cleanup_launcher(base_dir, expected_binary)
        except (OSError, RuntimeError, ValueError) as exc:
            _error("failed to stop seekdb launcher: {}".format(exc))
            return 1
        if not getattr(args, "quiet", False):
            print("stopped")
        return 0

    try:
        terminate_pid(pid, base_dir, expected_binary)
    except (OSError, RuntimeError) as exc:
        _error("failed to stop seekdb pid={}: {}".format(pid, exc))
        return 1

    remove_pid_file(pid_file)

    try:
        cleanup_launcher(base_dir, expected_binary)
    except (OSError, RuntimeError, ValueError) as exc:
        _error("failed to stop seekdb launcher: {}".format(exc))
        return 1

    if not getattr(args, "quiet", False):
        print("stopped")
    return 0


def command_destroy(args):
    base_dir = _base_dir(args.base_dir)
    try:
        validate_base_dir(base_dir)
    except (OSError, ValueError) as exc:
        _error("unsafe base-dir {}: {}".format(base_dir, exc))
        return 1

    if not base_dir.exists():
        print("destroyed")
        return 0
    if not base_dir.is_dir():
        _error("base-dir is not a directory: {}".format(base_dir))
        return 1

    try:
        read_instance_binary(base_dir)
    except (OSError, ValueError) as exc:
        _error("unsafe base-dir {}: {}".format(base_dir, exc))
        return 1

    stop_args = argparse.Namespace(
        base_dir=str(base_dir), quiet=True, require_match=True
    )
    if command_stop(stop_args) != 0:
        return 1

    try:
        shutil.rmtree(str(base_dir))
    except OSError as exc:
        _error("failed to remove {}: {}".format(base_dir, exc))
        return 1

    print("destroyed")
    return 0


def positive_float(value):
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def create_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="start seekdb and return immediately")
    start.add_argument("--binary", required=True, help="seekdb executable")
    start.add_argument("--launcher", help="structured executable launcher")
    start.add_argument("--base-dir", required=True, help="seekdb base directory")
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument(
        "--parameter",
        action="append",
        default=[],
        help="seekdb parameter value; may be specified more than once",
    )
    start.add_argument(
        "--nodaemon",
        action="store_true",
        help="pass --nodaemon to seekdb",
    )
    start.set_defaults(handler=command_start)

    ready = subparsers.add_parser(
        "wait-ready", help="wait until the managed seekdb accepts SELECT 1"
    )
    ready.add_argument("--client", default="obclient", help="SQL client executable")
    ready.add_argument("--launcher", help="structured executable launcher")
    ready.add_argument("--base-dir", required=True, help="seekdb base directory")
    ready.add_argument("--host", default="127.0.0.1")
    ready.add_argument("--port", type=int, default=DEFAULT_PORT)
    ready.add_argument("--user", default="root")
    ready.add_argument("--timeout", type=positive_float, default=DEFAULT_READY_TIMEOUT)
    ready.add_argument("--interval", type=positive_float, default=DEFAULT_READY_INTERVAL)
    ready.set_defaults(handler=command_wait_ready)

    stop = subparsers.add_parser("stop", help="stop seekdb using its pid file")
    stop.add_argument("--base-dir", required=True, help="seekdb base directory")
    stop.set_defaults(handler=command_stop)

    destroy = subparsers.add_parser(
        "destroy", help="stop seekdb and remove its base directory"
    )
    destroy.add_argument("--base-dir", required=True, help="seekdb base directory")
    destroy.set_defaults(handler=command_destroy)

    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
