#!/usr/bin/env python3
"""Launch one validated run-local macOS executable through LLDB."""

import argparse
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
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
DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60


class LauncherError(RuntimeError):
    """Indicate an unsafe launcher input or unavailable LLDB runtime."""


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
        *, target_stdout_fd: int = None,
        target_stderr_fd: int = None) -> list[str]:
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
        "((m is not None) or (0<=s<=255)) else 125; os._exit(c)")
    launch = "process launch --stop-at-entry"
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


def _terminate_group(process: subprocess.Popen) -> None:
    """Boundedly terminate, kill, and reap one LLDB process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def launch(
        binary: Path, arguments: Sequence[str],
        *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Run a validated binary through LLDB and propagate exit or signal status."""
    validate_lldb()
    environment = dict(os.environ)
    environment.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    target_stdout_fd = os.dup(sys.stdout.fileno())
    target_stderr_fd = os.dup(sys.stderr.fileno())
    try:
        process = subprocess.Popen(
            lldb_command(
                binary, arguments,
                target_stdout_fd=target_stdout_fd,
                target_stderr_fd=target_stderr_fd),
            env=environment, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(target_stdout_fd, target_stderr_fd),
            start_new_session=True)
    finally:
        os.close(target_stdout_fd)
        os.close(target_stderr_fd)
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_group(process)
        return 124
    except BaseException:
        _terminate_group(process)
        raise
    return 128 - return_code if return_code < 0 else return_code


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
    try:
        options = parse_args(arguments)
        binary = validate_binary(options.binary)
        return launch(binary, options.arguments, timeout_seconds=options.timeout)
    except (LauncherError, OSError, UnicodeError):
        return 125


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
