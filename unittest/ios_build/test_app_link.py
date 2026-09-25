"""Check the signed probe's engine dependency extraction without Apple tools."""
import importlib.util
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_app", ROOT / "deps/ios-build/build_app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class AppLinkTests(unittest.TestCase):
    """Protect archive resolution and reject silently incomplete link inputs."""

    def test_dependencies_preserve_order_and_omit_host_rpath(self):
        """Retain framework flags and resolve archives with spaces in their path."""
        with tempfile.TemporaryDirectory(prefix="ios app ") as temporary:
            directory = Path(temporary).resolve()
            archive = directory / "libseekdb_ios_runtime.a"
            archive.touch()
            command = ("clang++ probe.o -o probe.app/probe libseekdb_ios_runtime.a "
                       "-lm -framework Accelerate -Wl,-rpath,/host/cache")
            self.assertEqual(APP.engine_link_arguments(command, directory),
                             [str(archive), "-lm", "-framework", "Accelerate"])

    def test_third_party_libraries_are_absolute(self):
        """Prevent Xcode library search paths from selecting a macOS OpenMP dylib."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for name in ("libseekdb_ios_runtime.a", "libomp.a"):
                (directory / name).touch()
            command = "clang++ probe.o -o probe libseekdb_ios_runtime.a -L. -lomp"
            self.assertEqual(APP.engine_link_arguments(command, directory),
                             [str(directory / "libseekdb_ios_runtime.a"), str(directory / "libomp.a")])

    def test_unknown_flags_are_rejected(self):
        """Fail instead of dropping future link requirements unnoticed."""
        with self.assertRaises(ValueError):
            APP.engine_link_arguments("clang++ probe.o -o probe -unexpected", ROOT)

    def test_missing_runtime_is_rejected(self):
        """Never generate a test app that accidentally omits the engine."""
        with self.assertRaises(ValueError):
            APP.engine_link_arguments("clang++ probe.o -o probe -lm", ROOT)

    def test_packaging_requires_the_requested_test_hook_mode(self):
        """Reject both hookless test packages and hook-enabled ordinary packages."""
        self.assertTrue(hasattr(APP, "require_test_hook_mode"), "test-hook guard is missing")
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            (engine / "CMakeCache.txt").write_text("SEEKDB_IOS_TEST_HOOKS:BOOL=OFF\n")
            with self.assertRaises(ValueError):
                APP.require_test_hook_mode(engine, True)
            APP.require_test_hook_mode(engine, False)
            (engine / "CMakeCache.txt").write_text("SEEKDB_IOS_TEST_HOOKS:BOOL=ON\n")
            APP.require_test_hook_mode(engine, True)
            with self.assertRaises(ValueError):
                APP.require_test_hook_mode(engine, False)

    def test_xcode_build_is_constrained_to_the_device_sdk(self):
        """Do not let an unavailable device destination fall back to a simulator build."""
        source = (ROOT / "deps/ios-build/build_app.py").read_text()
        self.assertIn('"-sdk", "iphoneos"', source)

    def test_app_includes_easy_runtime_headers(self):
        """Keep clean App compilation independent of stale Xcode object files."""
        source = (ROOT / "unittest/ios_build/app/CMakeLists.txt").read_text()
        self.assertIn('src/oblib/easy"', source)
        self.assertIn('src/oblib/easy/include', source)
        self.assertIn('src/query/api', source)
        self.assertIn('/../../.."', source)
        self.assertIn('generated/share/inner_table', source)
        self.assertIn('ENGINE_HEADER_INCLUDE', source)
        self.assertIn('_NO_EXCEPTION', source)
        self.assertIn('DEFAULT_LOG_LEVEL=OB_LOG_LEVEL_ERROR', source)
        self.assertIn('CXX_STANDARD 20', source)
        self.assertIn('CXX_EXTENSIONS YES', source)

    def test_engine_header_include_comes_from_the_verified_build_cache(self):
        """Reuse the engine's portable dependency-header prefix for App sources."""
        self.assertTrue(hasattr(APP, "engine_header_include"))
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary) / "engine"
            include = Path(temporary) / "headers/include"
            engine.mkdir()
            include.mkdir(parents=True)
            (engine / "CMakeCache.txt").write_text(
                "SEEKDB_IOS_HEADER_PREFIX:PATH="
                + str(include.parent) + "\n")
            self.assertEqual(APP.engine_header_include(engine), include.resolve())

    def test_xcode_build_cleans_stale_link_output(self):
        """Force a relink because Xcode does not track the engine response file."""
        self.assertTrue(
            hasattr(APP, "xcode_build_command"),
            "the App packager must expose its deterministic Xcode command")
        command = APP.xcode_build_command(Path("/tmp/app"), "physical-device")
        self.assertEqual(command[-2:], ("clean", "build"))

    def test_packaged_app_rejects_stale_source_revision(self):
        """Reject a signed App whose executable did not relink the current engine."""
        self.assertTrue(
            hasattr(APP, "require_packaged_app_identity"),
            "the App packager must verify the executable before installation")
        output = (
            "SEEKDB_IOS_ARTIFACT_BUILD_ID=aaaaaaaaaaaa;"
            "SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled\n")
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "SeekDBProbe.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "SeekDBProbe",
            }))
            (app / "SeekDBProbe").touch()
            with mock.patch.object(APP.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=output)
                with self.assertRaises(ValueError):
                    APP.require_packaged_app_identity(
                        app, "bbbbbbbbbbbb", True)

    def test_packaged_app_identity_is_checked_before_install(self):
        """Fail closed on a stale linked executable before device installation."""
        source = (ROOT / "deps/ios-build/build_app.py").read_text()
        main = source[source.index("def main()") :]
        self.assertIn("require_packaged_app_identity(", main)
        self.assertLess(
            main.index("require_packaged_app_identity("),
            main.index("if options.install:"))

    def test_stale_app_is_never_verified_or_installed(self):
        """Stop the install path when a successful build leaves a stale App."""
        current = (
            "SEEKDB_IOS_ARTIFACT_BUILD_ID=bbbbbbbbbbbb;"
            "SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled\n")
        stale = (
            "SEEKDB_IOS_ARTIFACT_BUILD_ID=aaaaaaaaaaaa;"
            "SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled\n")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            engine = root / "engine"
            observer = engine / "src/observer"
            link_directory = observer / "CMakeFiles/seekdb_ios_link_check.dir"
            header_prefix = root / "headers"
            app = engine / "app/Release-iphoneos/SeekDBProbe.app"
            link_directory.mkdir(parents=True)
            (header_prefix / "include").mkdir(parents=True)
            app.mkdir(parents=True)
            runtime = observer / "libseekdb_ios_runtime.a"
            rust = observer / "libsql_nio.a"
            runtime.touch()
            rust.touch()
            (engine / "CMakeCache.txt").write_text(
                "SEEKDB_IOS_TEST_HOOKS:BOOL=ON\n"
                "SEEKDB_IOS_HEADER_PREFIX:PATH=" + str(header_prefix) + "\n")
            (link_directory / "link.txt").write_text(
                "clang++ probe.o -o probe libseekdb_ios_runtime.a libsql_nio.a")
            (app / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "SeekDBProbe",
            }))
            executable = app / "SeekDBProbe"
            executable.touch()
            commands = []

            def run(command, **_arguments):
                """Return controlled identities and record external side effects."""
                commands.append(tuple(str(item) for item in command))
                if command[0] == "/usr/bin/strings":
                    artifact = Path(command[-1])
                    if artifact == runtime:
                        return mock.Mock(returncode=0, stdout=current)
                    if artifact == rust:
                        return mock.Mock(
                            returncode=0, stdout="nio_device_test_count\n")
                    if artifact == executable:
                        return mock.Mock(returncode=0, stdout=stale)
                return mock.Mock(returncode=0, stdout="")

            arguments = [
                "build_app.py", "--team", "ABCDEFGHIJ", "--device",
                "physical-device", "--bundle-id", "org.seekdb.test",
                "--engine-build", str(engine), "--test-hooks", "--install",
            ]
            with mock.patch.object(APP, "source_build_id", return_value="bbbbbbbbbbbb"), \
                    mock.patch.object(APP.subprocess, "run", side_effect=run), \
                    mock.patch.object(sys, "argv", arguments):
                with self.assertRaisesRegex(ValueError, "different source revision"):
                    APP.main()

            self.assertFalse(any(command[0] == "codesign" for command in commands))
            self.assertFalse(any(command[:2] == ("xcrun", "devicectl")
                                 for command in commands))

    def test_artifact_metadata_rejects_stale_hook_mode(self):
        """Use the linked archive marker instead of trusting a reconfigured cache."""
        output = "SEEKDB_IOS_ARTIFACT_BUILD_ID=0123456789ab;SEEKDB_IOS_ARTIFACT_HOOK_MODE=disabled\n"
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            archive = engine / "src/observer/libseekdb_ios_runtime.a"
            archive.parent.mkdir(parents=True)
            archive.touch()
            with mock.patch.object(APP.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=output)
                with self.assertRaises(ValueError):
                    APP.require_artifact_identity(engine, "0123456789ab", True)

    def test_artifact_metadata_rejects_stale_source_revision(self):
        """Reject packaging when linked engine objects belong to another revision."""
        output = "SEEKDB_IOS_ARTIFACT_BUILD_ID=aaaaaaaaaaaa;SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled\n"
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            archive = engine / "src/observer/libseekdb_ios_runtime.a"
            archive.parent.mkdir(parents=True)
            archive.touch()
            with mock.patch.object(APP.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=output)
                with self.assertRaises(ValueError):
                    APP.require_artifact_identity(engine, "bbbbbbbbbbbb", True)
