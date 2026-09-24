#!/usr/bin/env python3
"""Run rustc and safely launch new macOS Cargo build scripts through LLDB."""

import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Optional, Sequence


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
        previous_names: frozenset[str]) -> Optional[Path]:
    """Return one new strict build-script output or reject unsafe ambiguity."""
    matches = []
    for candidate in output_directory.iterdir():
        if (candidate.name in previous_names
                or BUILD_SCRIPT_NAME.fullmatch(candidate.name) is None):
            continue
        if candidate.is_symlink():
            raise WrapperError("new build-script output is a symlink")
        if candidate.resolve(strict=True).parent != output_directory:
            raise WrapperError("new build-script output escaped its directory")
        matches.append(candidate)
    if len(matches) > 1:
        raise WrapperError("multiple new build-script outputs are ambiguous")
    return matches[0] if matches else None


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
    output_directory = None
    previous_names = frozenset()
    path_error = None
    if crate_name == "build_script_build":
        try:
            if output_value is None:
                raise WrapperError("build-script output directory is missing")
            output_directory = _canonical_output_directory(output_value)
            previous_names = frozenset(
                path.name for path in output_directory.iterdir())
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
    build_script = _new_build_script(output_directory, previous_names)
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
    result = subprocess.run([
        str(xcrun), "lldb", "--batch", "-o", "run",
        "-o", (
            "script import os,lldb; "
            "os._exit(lldb.debugger.GetSelectedTarget().GetProcess()"
            ".GetExitStatus())"),
        "--", str(real_build_script), *arguments,
    ], check=False, env=environment)
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
