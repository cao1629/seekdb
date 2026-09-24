#!/usr/bin/env python3
"""Run rustc and safely launch new macOS Cargo build scripts through LLDB."""

import hashlib
import json
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


def _fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes for one completed transaction step."""
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_digest(path: Path) -> str:
    """Return the SHA-256 digest of one regular non-symlink file."""
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise WrapperError("build-script transaction file is unsafe")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    """Persist one regular file before its durable transaction begins."""
    if path.is_symlink() or not path.is_file():
        raise WrapperError("build-script transaction file is unsafe")
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _optional_digest(path: Path) -> Optional[str]:
    """Return a safe file digest, or None when the path does not exist."""
    if not os.path.lexists(path):
        return None
    return _file_digest(path)


def _is_exact_launcher(build_script: Path, real_build_script: Path) -> bool:
    """Return whether a regular executable is the tracked exact launcher."""
    try:
        status = build_script.lstat()
        if (stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode)
                or status.st_mode & 0o111 == 0):
            return False
        return build_script.read_text(encoding="utf-8") == _launcher_source(
            real_build_script)
    except (OSError, UnicodeError):
        return False


def _rewrap_manifest_path(build_script: Path) -> Path:
    """Return the deterministic transaction manifest for one Cargo unit."""
    return build_script.with_name(f".{build_script.name}.rewrap.json")


def _write_rewrap_manifest(
        path: Path, record: Mapping[str, str],
        *, replace_existing: bool = False) -> None:
    """Atomically persist a validated rewrap transaction before any rename."""
    if os.path.lexists(path) != replace_existing:
        raise WrapperError("build-script rewrap transaction already exists")
    if replace_existing and (path.is_symlink() or not path.is_file()):
        raise WrapperError("build-script rewrap manifest is unsafe")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.tmp-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _read_rewrap_manifest(
        build_script: Path, manifest: Path) -> Mapping[str, str]:
    """Load one transaction while rejecting paths outside the unit directory."""
    try:
        if manifest.is_symlink() or not manifest.is_file():
            raise WrapperError("build-script rewrap manifest is unsafe")
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WrapperError("build-script rewrap manifest is invalid") from error
    required = {
        "schema", "state", "build_script", "real", "backup",
        "launcher_temporary", "old_digest", "new_digest",
    }
    if (not isinstance(record, dict) or set(record) != required
            or record.get("schema") != "seekdb-rustc-lldb-rewrap-v1"
            or record.get("state") not in {"prepared", "ready"}
            or record.get("build_script") != build_script.name
            or record.get("real") != f"{build_script.name}.real"):
        raise WrapperError("build-script rewrap manifest is invalid")
    backup = record.get("backup")
    launcher_temporary = record.get("launcher_temporary")
    if (not isinstance(backup, str) or Path(backup).name != backup
            or re.fullmatch(
                rf"[.]{re.escape(build_script.name)}[.]previous-"
                r"[0-9a-f]{16}[.]real", backup) is None
            or launcher_temporary
            != f".{build_script.name}.launcher.tmp"):
        raise WrapperError("build-script rewrap manifest path is unsafe")
    if re.fullmatch(r"[0-9a-f]{64}", str(record.get("old_digest"))) is None:
        raise WrapperError("build-script rewrap manifest is invalid")
    new_digest = record.get("new_digest")
    if ((record["state"] == "prepared" and new_digest != "")
            or (record["state"] == "ready" and re.fullmatch(
                r"[0-9a-f]{64}", str(new_digest)) is None)):
        raise WrapperError("build-script rewrap manifest is invalid")
    return record


def _write_transaction_launcher(path: Path, real_build_script: Path) -> None:
    """Create or validate the deterministic durable launcher temporary."""
    source = _launcher_source(real_build_script)
    if os.path.lexists(path):
        try:
            status = path.lstat()
            if (stat.S_ISLNK(status.st_mode)
                    or not stat.S_ISREG(status.st_mode)
                    or path.read_text(encoding="utf-8") != source):
                raise WrapperError("build-script launcher temporary is unsafe")
        except (OSError, UnicodeError) as error:
            raise WrapperError(
                "build-script launcher temporary is unsafe") from error
        return
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    try:
        os.fchmod(descriptor, 0o755)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _recover_rewrap(
        build_script: Path, *, promote_prepared: bool = True) -> None:
    """Complete a durable healthy-pair refresh or reject uncertain state."""
    manifest = _rewrap_manifest_path(build_script)
    if not os.path.lexists(manifest):
        return
    record = _read_rewrap_manifest(build_script, manifest)
    real = build_script.with_name(record["real"])
    backup = build_script.with_name(record["backup"])
    launcher_temporary = build_script.with_name(
        record["launcher_temporary"])
    old_digest = record["old_digest"]
    new_digest = record["new_digest"]
    if record["state"] == "prepared":
        if (os.path.lexists(backup) or os.path.lexists(launcher_temporary)
                or _optional_digest(real) != old_digest):
            raise WrapperError("prepared build-script transaction is inconsistent")
        if _is_exact_launcher(build_script, real):
            manifest.unlink()
            _fsync_directory(build_script.parent)
            return
        if (promote_prepared and os.path.lexists(build_script)
                and _is_host_macho_executable(build_script)):
            new_digest = _file_digest(build_script)
            ready_record = dict(record)
            ready_record["state"] = "ready"
            ready_record["new_digest"] = new_digest
            _fsync_file(build_script)
            _write_rewrap_manifest(
                manifest, ready_record, replace_existing=True)
        else:
            if os.path.lexists(build_script):
                status = build_script.lstat()
                if (stat.S_ISLNK(status.st_mode)
                        or not stat.S_ISREG(status.st_mode)):
                    raise WrapperError(
                        "prepared build-script output is unsafe")
            _write_transaction_launcher(launcher_temporary, real)
            os.replace(launcher_temporary, build_script)
            _fsync_directory(build_script.parent)
            manifest.unlink()
            _fsync_directory(build_script.parent)
            return
    for _step in range(4):
        build_digest = _optional_digest(build_script)
        real_digest = _optional_digest(real)
        backup_digest = _optional_digest(backup)
        if (_is_exact_launcher(build_script, real)
                and real_digest == new_digest
                and backup_digest == old_digest):
            if os.path.lexists(launcher_temporary):
                raise WrapperError("build-script transaction has stale temporary")
            manifest.unlink()
            _fsync_directory(build_script.parent)
            return
        if (build_digest == new_digest and real_digest == old_digest
                and backup_digest is None):
            os.replace(real, backup)
            _fsync_directory(build_script.parent)
            continue
        if (build_digest == new_digest and real_digest is None
                and backup_digest == old_digest):
            os.replace(build_script, real)
            _fsync_directory(build_script.parent)
            continue
        if (build_digest is None and real_digest == new_digest
                and backup_digest == old_digest):
            _write_transaction_launcher(launcher_temporary, real)
            os.replace(launcher_temporary, build_script)
            _fsync_directory(build_script.parent)
            continue
        raise WrapperError("build-script rewrap transaction is inconsistent")
    raise WrapperError("build-script rewrap transaction did not complete")


def _healthy_pair_state(build_script: Path) -> Optional[Mapping[str, object]]:
    """Validate the expected precompile unit and describe a healthy pair."""
    real = build_script.with_name(f"{build_script.name}.real")
    build_exists = os.path.lexists(build_script)
    real_exists = os.path.lexists(real)
    if build_exists and real_exists:
        if (not _is_exact_launcher(build_script, real)
                or not _is_host_macho_executable(real)):
            raise WrapperError("build-script precompile pair is damaged")
        return {
            "launcher_identity": _file_identity(build_script),
            "real_identity": _file_identity(real),
            "real_digest": _file_digest(real),
        }
    if real_exists:
        raise WrapperError("build-script precompile preserved state is stale")
    if build_exists:
        if build_script.is_symlink():
            raise WrapperError("build-script precompile output is unsafe")
        try:
            source = build_script.read_text(encoding="utf-8")
            if (source == _launcher_source(real)
                    or "--run-build-script" in source):
                raise WrapperError("build-script precompile launcher is damaged")
        except UnicodeError:
            pass
    return None


def _refresh_healthy_pair(
        build_script: Path, prestate: Mapping[str, object]) -> None:
    """Transactionally replace a healthy pair after rustc refreshes its raw."""
    real = build_script.with_name(f"{build_script.name}.real")
    if (not os.path.lexists(build_script) or build_script.is_symlink()
            or not _is_host_macho_executable(build_script)
            or _file_identity(build_script) == prestate["launcher_identity"]
            or not os.path.lexists(real)
            or _file_identity(real) != prestate["real_identity"]):
        raise WrapperError("rustc did not safely refresh the healthy pair")
    old_digest = str(prestate["real_digest"])
    new_digest = _file_digest(build_script)
    manifest = _rewrap_manifest_path(build_script)
    record = _read_rewrap_manifest(build_script, manifest)
    if (record["state"] != "prepared"
            or record["old_digest"] != old_digest):
        raise WrapperError("build-script rewrap intent is incompatible")
    _fsync_file(build_script)
    ready_record = dict(record)
    ready_record["state"] = "ready"
    ready_record["new_digest"] = new_digest
    _write_rewrap_manifest(manifest, ready_record, replace_existing=True)
    _recover_rewrap(build_script)


def _prepare_healthy_pair(
        build_script: Path, prestate: Mapping[str, object]) -> None:
    """Persist healthy precompile identity before rustc can replace it."""
    transaction_digest = hashlib.sha256(repr((
        prestate["launcher_identity"], prestate["real_identity"],
    )).encode("utf-8")).hexdigest()[:16]
    backup_name = f".{build_script.name}.previous-{transaction_digest}.real"
    launcher_temporary_name = f".{build_script.name}.launcher.tmp"
    if (os.path.lexists(build_script.with_name(backup_name))
            or os.path.lexists(build_script.with_name(
                launcher_temporary_name))):
        raise WrapperError("build-script rewrap destination is occupied")
    _write_rewrap_manifest(_rewrap_manifest_path(build_script), {
        "schema": "seekdb-rustc-lldb-rewrap-v1",
        "state": "prepared",
        "build_script": build_script.name,
        "real": f"{build_script.name}.real",
        "backup": backup_name,
        "launcher_temporary": launcher_temporary_name,
        "old_digest": str(prestate["real_digest"]),
        "new_digest": "",
    })


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
    healthy_prestate = None
    path_error = None
    if crate_name == "build_script_build":
        try:
            if output_value is None:
                raise WrapperError("build-script output directory is missing")
            output_directory = _canonical_output_directory(output_value)
            if expected_name is not None:
                expected = output_directory / expected_name
                _recover_rewrap(expected)
                healthy_prestate = _healthy_pair_state(expected)
            previous_snapshot = _candidate_snapshot(
                output_directory, expected_name)
        except WrapperError as error:
            path_error = error
    if path_error is not None:
        raise path_error
    healthy_transaction = (
        healthy_prestate is not None and sys.platform == "darwin")
    if healthy_transaction:
        _prepare_healthy_pair(
            output_directory / expected_name, healthy_prestate)
    compile_result = subprocess.run(
        [real_rustc, *rustc_arguments], check=False)
    if compile_result.returncode != 0:
        if healthy_transaction:
            _recover_rewrap(
                output_directory / expected_name, promote_prepared=False)
        if compile_result.returncode < 0:
            return 128 - compile_result.returncode
        return compile_result.returncode
    if sys.platform != "darwin":
        return 0
    if crate_name != "build_script_build":
        return 0
    if healthy_prestate is not None:
        _refresh_healthy_pair(
            output_directory / expected_name, healthy_prestate)
        return 0
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
