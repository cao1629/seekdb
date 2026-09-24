"""Check the signed probe's engine dependency extraction without Apple tools."""
import importlib.util
from pathlib import Path
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
