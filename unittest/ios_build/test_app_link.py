"""Check the signed probe's engine dependency extraction without Apple tools."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

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
