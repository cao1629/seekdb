"""Reject application-specific identifiers from tracked repository assets."""

import os
from pathlib import Path
import subprocess
import unicodedata
import unittest


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IDENTIFIERS = (
    "quick" + "lang",
    "_".join(("ql", "ios", "probe")),
)


def tracked_paths(root):
    """Return every tracked path relative to the repository root."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def tracked_text_matches(root, identifiers):
    """Return tracked text paths whose contents match any identifier."""
    command = ["git", "grep", "-Iilz"]
    for identifier in identifiers:
        command.extend(("-e", identifier))
    command.append("--")
    result = subprocess.run(command, cwd=root, capture_output=True)
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


class ProductNeutralityTests(unittest.TestCase):
    """Ensure reusable validation assets remain independent of one application."""

    def test_tracked_assets_are_product_neutral(self):
        """Report every tracked path or text file containing a forbidden identifier."""
        violations = {}

        for relative_path in tracked_paths(ROOT):
            normalized_path = unicodedata.normalize("NFKC", relative_path).casefold()
            if any(identifier in normalized_path for identifier in FORBIDDEN_IDENTIFIERS):
                violations.setdefault(relative_path, set()).add("path")

        for relative_path in tracked_text_matches(ROOT, FORBIDDEN_IDENTIFIERS):
            violations.setdefault(relative_path, set()).add("content")

        details = [
            f"{path} ({', '.join(source for source in ('path', 'content') if source in sources)})"
            for path, sources in sorted(violations.items())
        ]
        self.assertFalse(
            violations,
            "Application-specific identifiers found in tracked assets:\n"
            + "\n".join(details),
        )


if __name__ == "__main__":
    unittest.main()
