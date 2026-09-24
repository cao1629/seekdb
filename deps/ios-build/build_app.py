#!/usr/bin/env python3
"""Package the verified native engine closure into a signed UIKit device probe."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]


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


def require_artifact_identity(engine, expected_build_id, expected_hooks):
    """Verify identity markers compiled into the linked iOS runtime archive."""
    archive = engine / "src/observer/libseekdb_ios_runtime.a"
    if not archive.is_file():
        raise FileNotFoundError(archive)
    result = subprocess.run(["/usr/bin/strings", "-a", str(archive)], check=True,
                            capture_output=True, text=True)
    marker = re.search(
        r"SEEKDB_IOS_ARTIFACT_BUILD_ID=([0-9a-f]{12});"
        r"SEEKDB_IOS_ARTIFACT_HOOK_MODE=(enabled|disabled)", result.stdout)
    if marker is None:
        raise ValueError("iOS runtime archive has no artifact identity marker")
    build_id, hook_mode = marker.groups()
    if build_id != expected_build_id:
        raise ValueError("iOS runtime archive belongs to a different source revision")
    expected_mode = "enabled" if expected_hooks else "disabled"
    if hook_mode != expected_mode:
        raise ValueError(f"iOS runtime archive must have test hooks {expected_mode}")
    return build_id, hook_mode


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
                 "-DDEVELOPMENT_TEAM=" + options.team, "-DPROBE_BUNDLE_ID=" + options.bundle_id,
                 "-DRUST_DEVICE_TESTS=" + ("ON" if options.test_hooks else "OFF")]
    subprocess.run(configure,
                   check=True, env=environment)
    subprocess.run(["xcodebuild", "-project", str(build / "SeekDBProbe.xcodeproj"),
                    "-scheme", "SeekDBProbe", "-configuration", "Release", "-sdk", "iphoneos",
                    "-derivedDataPath", str(build / "DerivedData"),
                    "-destination", "id=" + options.device, "-allowProvisioningUpdates",
                    "-allowProvisioningDeviceRegistration", "build"], check=True, env=environment)
    app = build / "Release-iphoneos/SeekDBProbe.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    if options.install:
        subprocess.run(["xcrun", "devicectl", "device", "install", "app", "--device",
                        options.device, "--timeout", "120", str(app)], check=True, env=environment)
    print("Signed probe:", app)


if __name__ == "__main__":
    main()
