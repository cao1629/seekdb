#!/usr/bin/env python3
"""Package the verified native engine closure into a signed UIKit device probe."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_MARKER = re.compile(
    r"SEEKDB_IOS_ARTIFACT_BUILD_ID=([0-9a-f]{12});"
    r"SEEKDB_IOS_ARTIFACT_HOOK_MODE=(enabled|disabled)")


def engine_link_arguments(command, directory):
    """Extract dependency arguments from CMake's link probe, rejecting unknown flags."""
    tokens = shlex.split(command)
    output = tokens.index("-o")
    dependencies = tokens[output + 2:]
    search_paths = [(directory / item[2:]).resolve() for item in dependencies if item.startswith("-L")]
    result = []
    index = 0
    while index < len(dependencies):
        item = dependencies[index]
        if item.endswith(".a"):
            archive = (directory / item).resolve()
            if not archive.is_file():
                raise FileNotFoundError(archive)
            result.append(str(archive))
        elif item == "-framework":
            index += 1
            result.extend([item, dependencies[index]])
        elif item.startswith("-l"):
            candidates = [path / ("lib" + item[2:] + ".a") for path in search_paths]
            archive = next((path for path in candidates if path.is_file()), None)
            if archive:
                result.append(str(archive))
            elif item in {"-lpthread", "-ldl", "-liconv", "-lm"}:
                result.append(item)
            else:
                raise FileNotFoundError("Missing target static library: " + item)
        elif item.startswith("-L"):
            pass  # Resolve third-party libraries above, avoiding Xcode host search paths.
        elif item.startswith("-Wl,-framework,"):
            result.append(item)
        elif item.startswith("-Wl,-rpath,"):
            pass  # Static dependencies do not need host checkout runtime paths.
        else:
            raise ValueError("Unexpected engine link argument: " + item)
        index += 1
    if not any(Path(item).name == "libseekdb_ios_runtime.a" for item in result):
        raise ValueError("The link command does not include the iOS runtime")
    return result


def require_test_hook_mode(engine, expected):
    """Reject packaging when the engine cache does not match the requested hook mode."""
    cache = engine / "CMakeCache.txt"
    if not cache.is_file():
        raise ValueError("engine build has no CMake cache")
    enabled = "SEEKDB_IOS_TEST_HOOKS:BOOL=ON" in cache.read_text().splitlines()
    if enabled != expected:
        requested = "enabled" if expected else "disabled"
        raise ValueError(f"engine build must have SEEKDB_IOS_TEST_HOOKS {requested}")


def engine_header_include(engine):
    """Return the dependency include directory verified by the engine configure."""
    cache = engine / "CMakeCache.txt"
    if not cache.is_file():
        raise ValueError("engine build has no CMake cache")
    prefix = None
    for line in cache.read_text().splitlines():
        if line.startswith("SEEKDB_IOS_HEADER_PREFIX:") and "=" in line:
            prefix = Path(line.split("=", 1)[1]).expanduser().resolve()
            break
    include = prefix / "include" if prefix is not None else None
    if include is None or not include.is_dir():
        raise ValueError("engine build has no valid dependency header prefix")
    return include


def source_build_id():
    """Return a short immutable source revision for device evidence."""
    dirty = subprocess.run(["git", "diff-index", "--quiet", "HEAD", "--"], cwd=ROOT)
    if dirty.returncode != 0:
        raise ValueError("tracked source changes must be committed before device packaging")
    result = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True)
    build_id = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{12}", build_id):
        raise ValueError("git returned an invalid source build identifier")
    return build_id


def _require_artifact_identity(artifact, expected_build_id, expected_hooks, kind):
    """Verify one linked artifact marker against the requested source and mode."""
    if not artifact.is_file():
        raise FileNotFoundError(artifact)
    result = subprocess.run(["/usr/bin/strings", "-a", str(artifact)], check=True,
                            capture_output=True, text=True)
    marker = ARTIFACT_MARKER.search(result.stdout)
    if marker is None:
        raise ValueError(f"{kind} has no artifact identity marker")
    build_id, hook_mode = marker.groups()
    if build_id != expected_build_id:
        raise ValueError(f"{kind} belongs to a different source revision")
    expected_mode = "enabled" if expected_hooks else "disabled"
    if hook_mode != expected_mode:
        raise ValueError(f"{kind} must have test hooks {expected_mode}")
    return build_id, hook_mode


def require_artifact_identity(engine, expected_build_id, expected_hooks):
    """Verify identity markers compiled into the linked iOS runtime archive."""
    archive = engine / "src/observer/libseekdb_ios_runtime.a"
    return _require_artifact_identity(
        archive, expected_build_id, expected_hooks, "iOS runtime archive")


def require_packaged_app_identity(app, expected_build_id, expected_hooks):
    """Verify the packaged executable relinked the requested engine artifact."""
    plist_path = app / "Info.plist"
    if not plist_path.is_file():
        raise FileNotFoundError(plist_path)
    metadata = plistlib.loads(plist_path.read_bytes())
    executable_name = metadata.get("CFBundleExecutable")
    if (not isinstance(executable_name, str)
            or re.fullmatch(r"[A-Za-z0-9._-]+", executable_name) is None):
        raise ValueError("iOS App has an invalid executable name")
    return _require_artifact_identity(
        app / executable_name, expected_build_id, expected_hooks,
        "iOS App executable")


def xcode_build_command(build, device):
    """Create a device build command that cannot reuse a stale linked executable."""
    return (
        "xcodebuild", "-project", str(build / "SeekDBProbe.xcodeproj"),
        "-scheme", "SeekDBProbe", "-configuration", "Release",
        "-sdk", "iphoneos", "-derivedDataPath", str(build / "DerivedData"),
        "-destination", "id=" + device, "-allowProvisioningUpdates",
        "-allowProvisioningDeviceRegistration", "clean", "build")


def require_rust_archive_mode(arguments, expected_device_tests):
    """Require exactly one Rust archive with symbols matching the requested App mode."""
    archives = [Path(argument) for argument in arguments
                if Path(argument).name == "libsql_nio.a"]
    if len(archives) != 1:
        raise ValueError("the App must link exactly one libsql_nio.a archive")
    result = subprocess.run(["/usr/bin/strings", "-a", str(archives[0])], check=True,
                            capture_output=True, text=True)
    symbols = set(result.stdout.splitlines())
    has_device_tests = bool({"nio_device_test_count", "_nio_device_test_count"} & symbols)
    if has_device_tests != expected_device_tests:
        requested = "test" if expected_device_tests else "production"
        raise ValueError(f"Rust archive does not match requested {requested} mode")
    return archives[0]


def main():
    """Generate an Xcode wrapper, provision it, and optionally install on a device."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--bundle-id", default="org.seekdb.iosprobe")
    parser.add_argument("--engine-build", type=Path, default=ROOT / "build_ios_arm64")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--test-hooks", action="store_true")
    options = parser.parse_args()
    if not re.fullmatch(r"[A-Z0-9]{10}", options.team):
        parser.error("team must be a 10-character Apple team identifier")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", options.bundle_id):
        parser.error("invalid bundle identifier")
    engine = options.engine_build.resolve()
    if not engine.is_relative_to(ROOT):
        parser.error("engine build must remain inside the seekdb checkout")
    require_test_hook_mode(engine, options.test_hooks)
    header_include = engine_header_include(engine)
    build_id = source_build_id()
    require_artifact_identity(engine, build_id, options.test_hooks)
    directory = engine / "src/observer"
    command = (directory / "CMakeFiles/seekdb_ios_link_check.dir/link.txt").read_text()
    arguments = engine_link_arguments(command, directory)
    require_rust_archive_mode(arguments, options.test_hooks)
    build = engine / "app"
    build.mkdir(parents=True, exist_ok=True)
    response = build / "engine-link.rsp"
    response.write_text("\n".join(json.dumps(argument) for argument in arguments) + "\n")
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    configure = ["cmake", "-G", "Xcode", "-S", str(ROOT / "unittest/ios_build/app"),
                 "-B", str(build), "-DCMAKE_SYSTEM_NAME=iOS", "-DCMAKE_OSX_SYSROOT=iphoneos",
                 "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=18.0",
                 "-DENGINE_LINK_RESPONSE=" + str(response),
                 "-DENGINE_BUILD_ROOT=" + str(engine),
                 "-DENGINE_HEADER_INCLUDE=" + str(header_include),
                 "-DDEVELOPMENT_TEAM=" + options.team, "-DPROBE_BUNDLE_ID=" + options.bundle_id,
                 "-DRUST_DEVICE_TESTS=" + ("ON" if options.test_hooks else "OFF")]
    subprocess.run(configure,
                   check=True, env=environment)
    subprocess.run(xcode_build_command(build, options.device),
                   check=True, env=environment)
    app = build / "Release-iphoneos/SeekDBProbe.app"
    require_packaged_app_identity(app, build_id, options.test_hooks)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    if options.install:
        subprocess.run(["xcrun", "devicectl", "device", "install", "app", "--device",
                        options.device, "--timeout", "120", str(app)], check=True, env=environment)
    print("Signed probe:", app)


if __name__ == "__main__":
    main()
