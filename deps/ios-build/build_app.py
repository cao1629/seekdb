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


def main():
    """Generate an Xcode wrapper, provision it, and optionally install on a device."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--bundle-id", default="org.seekdb.iosprobe")
    parser.add_argument("--engine-build", type=Path, default=ROOT / "build_ios_arm64")
    parser.add_argument("--install", action="store_true")
    options = parser.parse_args()
    if not re.fullmatch(r"[A-Z0-9]{10}", options.team):
        parser.error("team must be a 10-character Apple team identifier")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", options.bundle_id):
        parser.error("invalid bundle identifier")
    engine = options.engine_build.resolve()
    if not engine.is_relative_to(ROOT):
        parser.error("engine build must remain inside the seekdb checkout")
    directory = engine / "src/observer"
    command = (directory / "CMakeFiles/seekdb_ios_link_check.dir/link.txt").read_text()
    arguments = engine_link_arguments(command, directory)
    build = engine / "app"
    build.mkdir(parents=True, exist_ok=True)
    response = build / "engine-link.rsp"
    response.write_text("\n".join(json.dumps(argument) for argument in arguments) + "\n")
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    subprocess.run(["cmake", "-G", "Xcode", "-S", str(ROOT / "unittest/ios_build/app"),
                    "-B", str(build), "-DCMAKE_SYSTEM_NAME=iOS", "-DCMAKE_OSX_SYSROOT=iphoneos",
                    "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=18.0",
                    "-DENGINE_LINK_RESPONSE=" + str(response),
                    "-DDEVELOPMENT_TEAM=" + options.team, "-DPROBE_BUNDLE_ID=" + options.bundle_id],
                   check=True, env=environment)
    subprocess.run(["xcodebuild", "-project", str(build / "SeekDBProbe.xcodeproj"),
                    "-scheme", "SeekDBProbe", "-configuration", "Release",
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
