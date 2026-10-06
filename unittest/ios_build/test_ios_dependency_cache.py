"""Verify installed dependency cache identities without downloads or compilation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

DRIVER_DIRECTORY = Path(__file__).resolve().parents[2] / "deps/ios-build"
sys.path.insert(0, str(DRIVER_DIRECTORY))
spec = importlib.util.spec_from_file_location("ios_dependency_builder", DRIVER_DIRECTORY / "build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DependencyCacheTests(unittest.TestCase):
    """Reject stale settings, partial installs, and changed artifact bytes."""

    def setUp(self):
        """Create a minimal installed package and matching build marker."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prefix = Path(self.temp.name)
        for relative in builder.PACKAGE_OUTPUTS["zlib"]:
            path = self.prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"installed output")
        self.identity = {"package": "zlib", "sdk": "iphoneos", "builder": "recipe", "deployment_target": "18.0"}
        self.marker = self.prefix / "verified.json"
        self.marker.write_text(json.dumps(dict(self.identity, outputs=builder.installed_output_hashes("zlib", self.prefix))))

    def test_matching_install_reused(self):
        """An unchanged complete package must be reusable."""
        self.assertTrue(builder.reusable_package(self.marker, self.identity, self.prefix))

    def test_settings_changes_rebuild(self):
        """SDK, recipe, and deployment changes must invalidate cached packages."""
        for field in ("sdk", "builder", "deployment_target"):
            with self.subTest(field=field):
                self.assertFalse(builder.reusable_package(self.marker, dict(self.identity, **{field: "changed"}), self.prefix))

    def test_changed_archive_rebuilds(self):
        """A modified archive cannot retain the old verification identity."""
        (self.prefix / "lib/libz.a").write_bytes(b"modified")
        self.assertFalse(builder.reusable_package(self.marker, self.identity, self.prefix))

    def test_missing_header_rebuilds(self):
        """A partial installation must not be accepted on archive presence alone."""
        (self.prefix / "include/zlib.h").unlink()
        self.assertFalse(builder.reusable_package(self.marker, self.identity, self.prefix))

    def test_invalid_marker_rebuilds(self):
        """Malformed and legacy metadata must trigger a fresh verification build."""
        for content in ("broken", "{}", "null"):
            self.marker.write_text(content)
            self.assertFalse(builder.reusable_package(self.marker, self.identity, self.prefix))


if __name__ == "__main__":
    unittest.main()
