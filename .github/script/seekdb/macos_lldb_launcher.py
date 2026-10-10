#!/usr/bin/env python3
"""Launch one validated run-local macOS executable through LLDB."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import time
from typing import Sequence


XCRUN = Path("/usr/bin/xcrun")
MACHO_64_MAGICS = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}
SYSTEM_DEPENDENCY_PREFIXES = ("/usr/lib/", "/System/Library/")
HOST_CPU_TYPES = {0x01000007, 0x0100000C}
MACHO_EXECUTE = 2
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
NON_PASSTHROUGH_SIGNALS = ("SIGSTOP", "SIGTSTP", "SIGTTIN", "SIGTTOU")
DEFAULT_TIMEOUT_SECONDS = 23 * 60 * 60
PROCESS_CLEANUP_GRACE_SECONDS = 0.5
TARGET_REAP_GRACE_SECONDS = 0.25
HANDLED_SIGNALS = tuple(
    item for item in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    if item is not None)
_ACTIVE_PROCESS = None
_PENDING_SIGNAL = 0


class LauncherError(RuntimeError):
    """Indicate an unsafe launcher input or unavailable LLDB runtime."""


class _ExternalTermination(BaseException):
    """Interrupt normal control flow after an external termination signal."""

    def __init__(self, signum: int):
        """Record the signal whose conventional shell status must be returned."""
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class _ProcessRecord:
    """Describe one process-table row used for bounded cleanup."""

    pid: int
    parent_pid: int
    group_id: int
    state: str
    command: str


def _handle_external_signal(signum: int, _frame) -> None:
    """Record one external signal and interrupt the main-thread operation."""
    global _PENDING_SIGNAL
    if _PENDING_SIGNAL:
        return
    _PENDING_SIGNAL = signum
    raise _ExternalTermination(signum)


def _install_signal_handlers() -> tuple[dict[int, object], set[int]]:
    """Install handlers while blocking their signals against setup races."""
    global _PENDING_SIGNAL
    _PENDING_SIGNAL = 0
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, HANDLED_SIGNALS)
    previous = {}
    try:
        for signum in HANDLED_SIGNALS:
            previous[signum] = signal.signal(signum, _handle_external_signal)
    except BaseException:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        raise
    return previous, previous_mask


def _restore_signal_handlers(
        previous: dict[int, object], original_mask: set[int]) -> int:
    """Transactionally restore handlers/mask and return a teardown signal."""
    global _PENDING_SIGNAL
    signal.pthread_sigmask(signal.SIG_BLOCK, HANDLED_SIGNALS)
    teardown_signal = _PENDING_SIGNAL
    try:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        pending = set(signal.sigpending()).intersection(HANDLED_SIGNALS)
        for signum in sorted(pending):
            signal.sigwait({signum})
            if not teardown_signal:
                teardown_signal = signum
        _PENDING_SIGNAL = 0
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
    return teardown_signal


def _directory_flags() -> int:
    """Return flags for one real directory component."""
    return (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0))


def _open_parent(path: Path) -> int:
    """Open every absolute parent component without following symlinks."""
    descriptor = os.open(os.path.sep, _directory_flags())
    try:
        for component in path.parent.parts[1:]:
            next_descriptor = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise LauncherError("launcher binary parent is unsafe") from error


def _is_macos_executable(descriptor: int) -> bool:
    """Validate a thin 64-bit host Mach-O executable from one anchored fd."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    header = os.read(descriptor, 32)
    if len(header) != 32:
        return False
    byte_order = MACHO_64_MAGICS.get(header[:4])
    if byte_order is None:
        return False
    (cpu_type, _cpu_subtype, file_type, command_count,
     command_size, _flags, _reserved) = struct.unpack(
        f"{byte_order}IIIIIII", header[4:32])
    if (cpu_type not in HOST_CPU_TYPES or file_type != MACHO_EXECUTE
            or command_size > 1024 * 1024):
        return False
    commands = os.read(descriptor, command_size)
    if len(commands) != command_size:
        return False
    offset = 0
    for _index in range(command_count):
        if offset + 8 > len(commands):
            return False
        command, size = struct.unpack(
            f"{byte_order}II", commands[offset:offset + 8])
        if size < 8 or offset + size > len(commands):
            return False
        if command == LC_VERSION_MIN_MACOSX and size >= 16:
            return True
        if command == LC_BUILD_VERSION and size >= 24:
            platform_id = struct.unpack(
                f"{byte_order}I", commands[offset + 8:offset + 12])[0]
            if platform_id == PLATFORM_MACOS:
                return True
        offset += size
    return False


def validate_binary(value: str) -> Path:
    """Return one absolute anchored thin 64-bit system-linked Mach-O path."""
    lexical = Path(value).expanduser()
    if not lexical.is_absolute() or ".." in lexical.parts:
        raise LauncherError("launcher binary path is not canonical")
    path = lexical.absolute()
    parent_fd = _open_parent(path)
    descriptor = None
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        linked = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (linked.st_dev, linked.st_ino)
                or not os.access(
                    path.name, os.X_OK, dir_fd=parent_fd,
                    follow_symlinks=False)
                or not _is_macos_executable(descriptor)):
            raise LauncherError("launcher binary is not a supported executable")
    except OSError as error:
        raise LauncherError("launcher binary is unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
    try:
        completed = subprocess.run(
            ["/usr/bin/otool", "-L", str(path)], capture_output=True,
            check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise LauncherError("launcher dependency validation failed") from error
    if completed.returncode != 0:
        raise LauncherError("launcher dependency validation failed")
    dependencies = [
        line.strip().split(" (", 1)[0]
        for line in completed.stdout.decode("utf-8", "strict").splitlines()[1:]
    ]
    if (not dependencies
            or any(not dependency.startswith(SYSTEM_DEPENDENCY_PREFIXES)
                   for dependency in dependencies)):
        raise LauncherError("launcher binary has unsupported dependencies")
    return path


def validate_lldb(*, xcrun: Path = XCRUN) -> None:
    """Require the fixed xcrun entrypoint to resolve a working LLDB."""
    if xcrun != XCRUN or not xcrun.is_file() or not os.access(xcrun, os.X_OK):
        raise LauncherError("xcrun is unavailable")
    environment = dict(os.environ)
    environment.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    for arguments in ((str(xcrun), "--find", "lldb"),
                      (str(xcrun), "lldb", "--version")):
        try:
            completed = subprocess.run(
                arguments, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise LauncherError("LLDB is unavailable") from error
        if completed.returncode != 0:
            raise LauncherError("LLDB is unavailable")


def lldb_command(
        binary: Path, arguments: Sequence[str],
        *, target_stdin_fd: int = None,
        target_stdout_fd: int = None,
        target_stderr_fd: int = None,
        target_status_fd: int = None) -> list[str]:
    """Build one quote-safe LLDB argv with exact signal and status mapping."""
    excluded_signals = repr(NON_PASSTHROUGH_SIGNALS)
    signal_policy = (
        "script import lldb; "
        "p=lldb.debugger.GetSelectedTarget().GetProcess(); "
        "u=p.GetUnixSignals(); "
        f"x={excluded_signals}; "
        "r=[(u.SetShouldStop(n,False),u.SetShouldNotify(n,False),"
        "u.SetShouldSuppress(n,False)) for i in range(u.GetNumSignals()) "
        "for n in [u.GetSignalAtIndex(i)] "
        "if u.GetSignalAsCString(n) not in x]")
    status_suffix = (
        f"os.write({target_status_fd},(str(c)+'\\n').encode())"
        if target_status_fd is not None else "c=c")
    status = (
        "script import os,re,signal,lldb; "
        "p=lldb.debugger.GetSelectedTarget().GetProcess(); "
        "d=p.GetExitDescription() or ''; "
        "m=re.search(r'[Ss]ignal (SIG[A-Z0-9]+|[0-9]+)',d); "
        "n=(int(m.group(1)) if m and m.group(1).isdigit() "
        "else int(getattr(signal,m.group(1)))) if m else None; "
        "s=p.GetExitStatus(); "
        "c=((128+n) if n is not None else s) "
        "if p.GetState()==lldb.eStateExited and "
        "((m is not None) or (0<=s<=255)) else 125; "
        + status_suffix)
    launch = "process launch --stop-at-entry"
    if target_stdin_fd is not None:
        launch += f" -i /dev/fd/{target_stdin_fd}"
    if target_stdout_fd is not None:
        launch += f" -o /dev/fd/{target_stdout_fd}"
    if target_stderr_fd is not None:
        launch += f" -e /dev/fd/{target_stderr_fd}"
    return [
        str(XCRUN), "lldb", "--no-lldbinit", "--batch", "--source-quietly",
        "-o", launch,
        "-o", signal_policy, "-o", "process continue", "-o", status,
        "-k", status, "--", str(binary), *arguments,
    ]


def _process_table() -> dict[int, _ProcessRecord]:
    """Read one bounded process table for identity-aware cleanup."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat=,command="],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False, text=True)
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    records = {}
    for line in completed.stdout.splitlines():
        values = line.split(maxsplit=4)
        if len(values) != 5:
            continue
        try:
            pid, parent_pid, group_id = (
                int(value) for value in values[:3])
        except ValueError:
            continue
        records[pid] = _ProcessRecord(
            pid, parent_pid, group_id, values[3], values[4])
    return records


def _descendant_ids(
        root_pid: int, records: dict[int, _ProcessRecord]) -> set[int]:
    """Return descendants whose current parent chain reaches one LLDB pid."""
    children = {}
    for record in records.values():
        children.setdefault(record.parent_pid, []).append(record.pid)
    descendants = set()
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        descendants.add(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _command_matches(record: _ProcessRecord, binary: Path) -> bool:
    """Return whether one current command is the exact validated target."""
    target = str(binary)
    return record.command == target or record.command.startswith(target + " ")


def _signal_records(
        records: dict[int, _ProcessRecord], signum: int,
        *, root_pid: int, target_binary: Path = None) -> None:
    """Signal only records whose identity and descendant chain still match."""
    current = _process_table()
    descendants = _descendant_ids(root_pid, current)
    matched = []
    for process_id, expected in records.items():
        actual = current.get(process_id)
        if (actual is None or actual.group_id != expected.group_id
                or actual.command != expected.command
                or process_id not in descendants):
            continue
        if target_binary is not None and not _command_matches(
                actual, target_binary):
            continue
        matched.append(actual)
    for record in matched:
        try:
            os.kill(record.pid, signum)
        except OSError:
            pass
    for group_id in {
            record.group_id for record in matched
            if record.pid == record.group_id and group_id_safe(record.group_id)}:
        try:
            os.killpg(group_id, signum)
        except OSError:
            pass


def group_id_safe(group_id: int) -> bool:
    """Reject special process-group identifiers before sending a signal."""
    return group_id > 1


def _wait_pids_absent(process_ids: set[int], timeout_seconds: float) -> bool:
    """Wait until every recorded pid is absent, including zombie entries."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_ids.intersection(_process_table()):
            return True
        time.sleep(0.05)
    return not process_ids.intersection(_process_table())


def _terminate_group(
        process: subprocess.Popen, target_binary: Path = None) -> None:
    """Freeze, terminate, kill, and reap LLDB plus target process groups."""
    try:
        os.killpg(process.pid, signal.SIGSTOP)
    except OSError:
        pass
    records = _process_table()
    descendant_ids = _descendant_ids(process.pid, records)
    target_records = {
        pid: records[pid] for pid in descendant_ids
        if target_binary is not None and _command_matches(
            records[pid], target_binary)
    }
    debugger_records = {
        pid: records[pid] for pid in descendant_ids
        if pid not in target_records
    }
    _signal_records(
        target_records, signal.SIGTERM, root_pid=process.pid,
        target_binary=target_binary)
    try:
        os.killpg(process.pid, signal.SIGCONT)
    except OSError:
        pass
    if not _wait_pids_absent(
            set(target_records), TARGET_REAP_GRACE_SECONDS):
        _signal_records(
            target_records, signal.SIGKILL, root_pid=process.pid,
            target_binary=target_binary)
        _wait_pids_absent(set(target_records), TARGET_REAP_GRACE_SECONDS)
    _signal_records(
        debugger_records, signal.SIGTERM, root_pid=process.pid)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    _signal_records(
        debugger_records, signal.SIGKILL, root_pid=process.pid)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _duplicate_standard_streams() -> tuple[int, int, int]:
    """Duplicate stdin/stdout/stderr and close partial results on failure."""
    descriptors = []
    try:
        for descriptor in (0, 1, 2):
            descriptors.append(os.dup(descriptor))
    except BaseException:
        for descriptor in descriptors:
            os.close(descriptor)
        raise
    return tuple(descriptors)


def _close_descriptors(descriptors: list[int]) -> None:
    """Close owned descriptors once even if signal handling interrupts close."""
    while descriptors:
        descriptor = descriptors.pop()
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_target_status(descriptor: int) -> int:
    """Read one bounded target status written before normal LLDB shutdown."""
    os.set_blocking(descriptor, False)
    try:
        payload = os.read(descriptor, 64)
    except BlockingIOError:
        return 125
    try:
        values = [int(value) for value in payload.decode("ascii").split()]
    except (UnicodeError, ValueError):
        return 125
    if not values or any(value != values[0] for value in values):
        return 125
    return values[0] if 0 <= values[0] <= 255 else 125


def launch(
        binary: Path, arguments: Sequence[str],
        *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Run a validated binary through LLDB and propagate exit or signal status."""
    validate_lldb()
    environment = dict(os.environ)
    environment.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    global _ACTIVE_PROCESS
    descriptors = []
    status_descriptors = []
    process = None
    try:
        descriptors = list(_duplicate_standard_streams())
        target_stdin_fd, target_stdout_fd, target_stderr_fd = descriptors
        status_read_fd, status_write_fd = os.pipe()
        status_descriptors = [status_read_fd, status_write_fd]
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, HANDLED_SIGNALS)
        try:
            process = subprocess.Popen(
                lldb_command(
                    binary, arguments,
                    target_stdin_fd=target_stdin_fd,
                    target_stdout_fd=target_stdout_fd,
                    target_stderr_fd=target_stderr_fd,
                    target_status_fd=status_write_fd),
                env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=tuple(descriptors + [status_write_fd]),
                start_new_session=True)
            _ACTIVE_PROCESS = process
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        _close_descriptors(descriptors)
        status_descriptors.remove(status_write_fd)
        os.close(status_write_fd)
        return_code = process.wait(timeout=timeout_seconds)
        if return_code != 0:
            return 128 - return_code if return_code < 0 else 125
        return _read_target_status(status_read_fd)
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_group(process, binary)
        return 124
    except BaseException:
        if process is not None:
            _terminate_group(process, binary)
        raise
    finally:
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None
        _close_descriptors(descriptors)
        _close_descriptors(status_descriptors)


def parse_args(arguments: Sequence[str]) -> argparse.Namespace:
    """Parse one strict binary plus opaque target argument vector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments)
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    if options.arguments[:1] == ["--"]:
        options.arguments = options.arguments[1:]
    return options


def main(arguments: Sequence[str]) -> int:
    """Validate one target and dispatch it through the fixed LLDB command."""
    previous_handlers, previous_mask = _install_signal_handlers()
    result = 125
    termination_signal = 0
    try:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            options = parse_args(arguments)
            binary = validate_binary(options.binary)
            result = launch(
                binary, options.arguments, timeout_seconds=options.timeout)
        except _ExternalTermination as termination:
            termination_signal = termination.signum
        except (LauncherError, OSError, UnicodeError):
            result = 125
    finally:
        while True:
            try:
                teardown_signal = _restore_signal_handlers(
                    previous_handlers, previous_mask)
                if not termination_signal:
                    termination_signal = teardown_signal
                break
            except _ExternalTermination as termination:
                if not termination_signal:
                    termination_signal = termination.signum
    return 128 + termination_signal if termination_signal else result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
