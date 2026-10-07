#!/usr/bin/env python3
"""Build a standalone framework probe and optionally sign it with an existing local profile."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(command, environment):
    """Run one build or signing operation, preserving its exact failure status."""
    subprocess.run([str(item) for item in command], env=environment, check=True)


def main():
    """Configure the isolated App, compile without account access, and sign local inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--simulator", action="store_true")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--bundle-id", default="org.seekdb.frameworkprobe")
    args = parser.parse_args()
    framework, build = args.framework.resolve(strict=True), args.build.resolve()
    if not framework.is_relative_to(ROOT) or not build.is_relative_to(ROOT):
        parser.error("Framework and build outputs must remain inside this repository")
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    manifest = json.loads((framework / "build-manifest.json").read_text())
    expected = "IOSSIMULATOR" if args.simulator else "IOS"
    if manifest["platform"] != expected or manifest["test_hooks"] != "disabled":
        parser.error("Framework platform or production hook state does not match")
    profile = None
    team = ""
    if not args.simulator:
        if args.profile is None or not args.identity:
            parser.error("A device build requires an existing local --profile and --identity")
        profile = plistlib.loads(subprocess.check_output(["security", "cms", "-D", "-i", str(args.profile)]))
        team = profile["TeamIdentifier"][0]
        application = profile["Entitlements"]["application-identifier"]
        args.bundle_id = application.split(".", 1)[1]
        if "*" in args.bundle_id:
            parser.error("Use an existing explicit App profile")
    sdk = "iphonesimulator" if args.simulator else "iphoneos"
    configure = ["cmake", "-G", "Xcode", "-S", ROOT / "unittest/ios_build/framework_probe", "-B", build,
                 "-DCMAKE_SYSTEM_NAME=iOS", "-DCMAKE_OSX_ARCHITECTURES=arm64",
                 "-DCMAKE_OSX_SYSROOT=" + sdk, "-DCMAKE_OSX_DEPLOYMENT_TARGET=" + manifest["deployment_target"],
                 "-DSEEKDB_FRAMEWORK=" + str(framework), "-DPROBE_BUNDLE_ID=" + args.bundle_id,
                 "-DDEVELOPMENT_TEAM=" + team,
                 "-DPROBE_SOURCE_REVISION=" + subprocess.check_output(
                     ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()]
    run(configure, environment)
    run(["xcodebuild", "-project", build / "SeekDBFrameworkProbe.xcodeproj", "-scheme", "SeekDBFrameworkProbe",
         "-configuration", "Release", "-sdk", sdk, "CODE_SIGNING_ALLOWED=NO", "build"], environment)
    app = build / ("Release-" + sdk) / "SeekDBFrameworkProbe.app"
    if not app.is_dir():
        raise ValueError("Built App is missing: " + str(app))
    embedded = app / "Frameworks/SeekDB.framework"
    shutil.copytree(framework, embedded, dirs_exist_ok=True)
    identity = args.identity if profile is not None else "-"
    run(["codesign", "--force", "--sign", identity, "--timestamp=none", embedded], environment)
    if profile is not None:
        shutil.copyfile(args.profile, app / "embedded.mobileprovision")
        entitlements = build / "probe-entitlements.plist"
        entitlements.write_bytes(plistlib.dumps(profile["Entitlements"]))
        run(["codesign", "--force", "--sign", args.identity, "--entitlements", entitlements,
             "--timestamp=none", app], environment)
        run(["codesign", "--verify", "--deep", "--strict", app], environment)
    else:
        run(["codesign", "--force", "--sign", "-", "--timestamp=none", app], environment)
    print("Probe App:", app)
    print("Bundle identifier:", args.bundle_id)


if __name__ == "__main__":
    main()
