#!/usr/bin/env python3
"""Run rustc and safely launch new macOS Cargo build scripts through LLDB."""

import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Mapping, Optional, Sequence


XCRUN = Path("/usr/bin/xcrun")
BUILD_SCRIPT_NAME = re.compile(r"build_script_build-[0-9a-f]+")
REAL_BUILD_SCRIPT_NAME = re.compile(r"build_script_build-[0-9a-f]+[.]real")
MACHO_64_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",
    b"\xfe\xed\xfa\xcf": ">",
}
HOST_CPU_TYPES = {0x01000007, 0x0100000C}
MACHO_EXECUTE = 2
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
NON_PASSTHROUGH_SIGNALS = (
    "SIGSTOP", "SIGTSTP", "SIGTTIN", "SIGTTOU",
)


class WrapperError(RuntimeError):
    """Indicate that a generated build-script path is unsafe to replace."""


def _option_value(arguments: Sequence[str], option: str) -> Optional[str]:
    """Return one unambiguous separate or equals-form option value."""
    values = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == option:
            if index + 1 >= len(arguments):
                raise WrapperError(f"missing value for {option}")
            values.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith(f"{option}="):
            values.append(argument.split("=", 1)[1])
        index += 1
    if not values:
        return None
    if len(set(values)) != 1:
        raise WrapperError(f"ambiguous {option}")
    return values[0]


def _canonical_output_directory(value: str) -> Path:
    """Resolve one absolute, direct, non-symlink rustc output directory."""
    lexical = Path(value)
    if not lexical.is_absolute() or ".." in lexical.parts:
        raise WrapperError("build-script output directory is not canonical")
    if lexical.is_symlink() or not lexical.is_dir():
        raise WrapperError("build-script output directory is unavailable")
    return lexical.resolve(strict=True)


def _extra_filename(arguments: Sequence[str]) -> Optional[str]:
    """Return one unambiguous rustc extra-filename codegen value."""
    values = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        setting = None
        if argument == "-C":
            if index + 1 >= len(arguments):
                raise WrapperError("missing value for -C")
            setting = arguments[index + 1]
            index += 2
        elif argument.startswith("-C"):
            setting = argument[2:]
            index += 1
        else:
            index += 1
        if setting is not None and setting.startswith("extra-filename="):
            values.append(setting.split("=", 1)[1])
    if not values:
        return None
    if len(set(values)) != 1:
        raise WrapperError("ambiguous rustc extra-filename")
    suffix = values[0]
    if re.fullmatch(r"-[0-9a-f]+", suffix) is None:
        raise WrapperError("rustc extra-filename is not a strict hash")
    return suffix


def _file_identity(path: Path) -> tuple[object, ...]:
    """Fingerprint one candidate strongly enough to detect replacement."""
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode):
        return ("symlink", os.readlink(path), status.st_mtime_ns)
    if not stat.S_ISREG(status.st_mode):
        return ("other", status.st_mode, status.st_mtime_ns)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return (
        "regular", status.st_dev, status.st_ino, status.st_size,
        status.st_mtime_ns, digest.hexdigest(),
    )


def _candidate_snapshot(
        output_directory: Path,
        expected_name: Optional[str]) -> Mapping[str, tuple[object, ...]]:
    """Capture strict candidates before or after one rustc invocation."""
    snapshot = {}
    for candidate in output_directory.iterdir():
        if BUILD_SCRIPT_NAME.fullmatch(candidate.name) is None:
            continue
        if expected_name is not None and candidate.name != expected_name:
            continue
        snapshot[candidate.name] = _file_identity(candidate)
    return snapshot


def _is_host_macho_executable(path: Path) -> bool:
    """Return whether a regular file is a 64-bit macOS host executable."""
    try:
        file_status = path.lstat()
        if (stat.S_ISLNK(file_status.st_mode)
                or not stat.S_ISREG(file_status.st_mode)
                or file_status.st_mode & 0o111 == 0):
            return False
        with path.open("rb") as stream:
            header = stream.read(32)
            if len(header) != 32:
                return False
            byte_order = MACHO_64_MAGICS.get(header[:4])
            if byte_order is None:
                return False
            (cpu_type, _cpu_subtype, file_type, command_count,
             command_size, _flags, _reserved) = struct.unpack(
                f"{byte_order}IIIIIII", header[4:32])
            if command_size > 1024 * 1024:
                return False
            commands = stream.read(command_size)
    except OSError:
        return False
    if (cpu_type not in HOST_CPU_TYPES or file_type != MACHO_EXECUTE
            or len(commands) != command_size):
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


def _new_build_script(
        output_directory: Path,
        previous_snapshot: Mapping[str, tuple[object, ...]],
        expected_name: Optional[str]) -> Optional[Path]:
    """Return one new strict build-script output or reject unsafe ambiguity."""
    current_snapshot = _candidate_snapshot(output_directory, expected_name)
    if expected_name is not None:
        expected = output_directory / expected_name
        preserved = expected.with_name(f"{expected.name}.real")
        if expected_name not in current_snapshot:
            raise WrapperError("expected build-script output is missing")
        if os.path.lexists(expected) and os.path.lexists(preserved):
            raise WrapperError(
                "current build-script output has stale preserved state")
        if (previous_snapshot.get(expected_name)
                == current_snapshot[expected_name]):
            raise WrapperError("rustc did not refresh its expected output")
        changed_names = [expected_name]
    else:
        changed_names = [
            name for name, identity in current_snapshot.items()
            if previous_snapshot.get(name) != identity
        ]
    if len(changed_names) > 1:
        raise WrapperError("multiple changed build-script outputs are ambiguous")
    if not changed_names:
        return None
    candidate = output_directory / changed_names[0]
    if expected_name is not None and candidate.name != expected_name:
        raise WrapperError("rustc changed an unexpected build-script output")
    if BUILD_SCRIPT_NAME.fullmatch(candidate.name) is None:
        raise WrapperError("new build-script output has an invalid name")
    try:
        if candidate.is_symlink():
            raise WrapperError("new build-script output is a symlink")
        if candidate.resolve(strict=True).parent != output_directory:
            raise WrapperError("new build-script output escaped its directory")
    except OSError as error:
        raise WrapperError("new build-script output is unavailable") from error
    return candidate


def _launcher_source(real_build_script: Path) -> str:
    """Create a quote-safe launcher bound to one preserved real executable."""
    wrapper = shlex.quote(str(Path(__file__).resolve()))
    real = shlex.quote(str(real_build_script))
    return (
        "#!/bin/sh\n"
        f"exec {wrapper} --run-build-script {real} \"$@\"\n"
    )


def _replace_with_launcher(build_script: Path) -> None:
    """Atomically preserve one real build script and install its launcher."""
    if not _is_host_macho_executable(build_script):
        return
    real_build_script = build_script.with_name(f"{build_script.name}.real")
    if os.path.lexists(real_build_script):
        raise WrapperError("preserved build-script output already exists")
    launcher_source = _launcher_source(real_build_script)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=build_script.parent,
                prefix=".rustc-lldb-launcher-", delete=False) as stream:
            temporary_name = Path(stream.name)
            stream.write(launcher_source)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_name.chmod(0o755)
        if build_script.is_symlink():
            raise WrapperError("build-script output changed into a symlink")
        os.replace(build_script, real_build_script)
        os.replace(temporary_name, build_script)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                temporary_name.unlink()
            except FileNotFoundError:
                pass


def compile_and_wrap(arguments: Sequence[str]) -> int:
    """Run real rustc, then wrap only one newly generated host build script."""
    if not arguments:
        raise WrapperError("real rustc command is missing")
    real_rustc = arguments[0]
    rustc_arguments = tuple(arguments[1:])
    crate_name = _option_value(rustc_arguments, "--crate-name")
    output_value = _option_value(rustc_arguments, "--out-dir")
    extra_filename = (
        _extra_filename(rustc_arguments)
        if crate_name == "build_script_build" else None)
    expected_name = (
        f"build_script_build{extra_filename}"
        if crate_name == "build_script_build" and extra_filename else None)
    output_directory = None
    previous_snapshot = {}
    path_error = None
    if crate_name == "build_script_build":
        try:
            if output_value is None:
                raise WrapperError("build-script output directory is missing")
            output_directory = _canonical_output_directory(output_value)
            previous_snapshot = _candidate_snapshot(
                output_directory, expected_name)
        except WrapperError as error:
            path_error = error
    compile_result = subprocess.run(
        [real_rustc, *rustc_arguments], check=False)
    if compile_result.returncode != 0:
        if compile_result.returncode < 0:
            return 128 - compile_result.returncode
        return compile_result.returncode
    if sys.platform != "darwin":
        return 0
    if crate_name != "build_script_build":
        return 0
    if path_error is not None:
        raise path_error
    build_script = _new_build_script(
        output_directory, previous_snapshot, expected_name)
    if build_script is not None:
        _replace_with_launcher(build_script)
    return 0


def _validated_real_build_script(value: str) -> Path:
    """Return one canonical preserved host build-script executable."""
    lexical = Path(value)
    if (not lexical.is_absolute() or ".." in lexical.parts
            or REAL_BUILD_SCRIPT_NAME.fullmatch(lexical.name) is None
            or lexical.is_symlink()):
        raise WrapperError("preserved build-script path is invalid")
    resolved = lexical.resolve(strict=True)
    if resolved.parent != lexical.parent.resolve(strict=True):
        raise WrapperError("preserved build-script escaped its directory")
    if not _is_host_macho_executable(resolved):
        raise WrapperError("preserved build-script is not a host executable")
    return resolved


def run_build_script(
        build_script: Path, arguments: Sequence[str],
        *, xcrun: Path = XCRUN) -> int:
    """Launch a preserved build script under LLDB and return its exit status."""
    real_build_script = _validated_real_build_script(str(build_script))
    environment = dict(os.environ)
    environment.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    excluded_signals = repr(NON_PASSTHROUGH_SIGNALS)
    signal_policy_script = (
        "script import lldb; "
        "p=lldb.debugger.GetSelectedTarget().GetProcess(); "
        "u=p.GetUnixSignals(); "
        f"x={excluded_signals}; "
        "[(u.SetShouldStop(n,False),u.SetShouldNotify(n,False),"
        "u.SetShouldSuppress(n,False)) "
        "for i in range(u.GetNumSignals()) "
        "for n in [u.GetSignalAtIndex(i)] "
        "if u.GetSignalAsCString(n) not in x]")
    status_script = (
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
        "os._exit(c)")
    result = subprocess.run([
        str(xcrun), "lldb", "--no-lldbinit", "--batch",
        "-o", "process launch --stop-at-entry",
        "-o", signal_policy_script,
        "-o", "process continue", "-o", status_script,
        "-k", status_script,
        "--", str(real_build_script), *arguments,
    ], check=False, env=environment)
    if result.returncode < 0:
        return 128 - result.returncode
    return result.returncode


def main(arguments: Sequence[str]) -> int:
    """Dispatch rustc-wrapper and preserved-build-script launcher modes."""
    try:
        if arguments and arguments[0] == "--run-build-script":
            if len(arguments) < 2:
                raise WrapperError("preserved build-script path is missing")
            return run_build_script(Path(arguments[1]), arguments[2:])
        return compile_and_wrap(arguments)
    except (OSError, WrapperError):
        return 125


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
