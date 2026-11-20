"""Keeping signatures and stable download links tied to the built version matters on a release retry."""

from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_assets", ROOT / "packaging/release_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class ReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        override = patch.object(assets, "ROOT", self.root)
        override.start()
        self.addCleanup(override.stop)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.root / "packaging").mkdir()
        (self.root / "packaging/VERSION").write_text("1.5.0\n", encoding="ascii")
        shutil.copytree(ROOT / "browser-extension", self.root / "browser-extension")
        (self.root / "docs").mkdir()
        (self.root / "docs/GUIDE.md").write_text("Как открыть QuizLens\n", encoding="utf-8")

    def make_archives(self, archive_version="1.5.0", repository="Example/quiz"):
        for name, prefix in assets.ARCHIVES.items():
            with zipfile.ZipFile(self.dist / name, "w") as package:
                package.writestr(prefix + "packaging/VERSION", archive_version + "\n")
                package.writestr(prefix + "packaging/REPOSITORY", repository + "\n")
                package.writestr(prefix.split("/")[0] + "/README.md", "Как открыть QuizLens\n")
                for filename in assets.EXTENSION_FILES:
                    content = (self.root / "browser-extension" / filename).read_bytes().replace(b"\r\n", b"\n")
                    package.writestr(prefix.split("/")[0] + "/browser-extension/" + filename, content)
                    if name == "QuizLens-Mac.zip":
                        package.writestr(prefix + "browser-extension/" + filename, content)

    def test_release_assets_and_status_marker(self):
        self.make_archives()
        for status in ("unsigned", "notarized"):
            with patch.object(assets.subprocess, "check_output", return_value="a" * 40):
                assets.assemble(self.dist, "v1.5.0", "Example/quiz", status)
            info = json.loads((self.dist / "release.json").read_bytes())
            self.assertIs(info["macos"]["notarized"], status == "notarized")
            self.assertEqual(info["tag"], "v1.5.0")
            self.assertIn("<!-- quizlens-macos: " + status + " -->", (self.dist / "release-notes.md").read_text(encoding="utf-8"))
            checksums = (self.dist / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
            self.assertEqual(len(checksums), 5)
            self.assertIn(assets.EXTENSION_ARCHIVE, info["sha256"])
            with zipfile.ZipFile(self.dist / assets.EXTENSION_ARCHIVE) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), {"browser-extension/" + n for n in assets.EXTENSION_FILES})
            for item in checksums:
                expected, name = item.split("  ", 1)
                self.assertEqual(expected, hashlib.sha256((self.dist / name).read_bytes()).hexdigest())

    def test_reject_inconsistent_inputs_before_assembling_assets(self):
        for tag, repo in (("v1.0.0", "Example/quiz"), ("v1.5.0", "../quiz"), ("v1.5.0", "Example/..")):
            with self.assertRaises(ValueError):
                assets.validate(tag, repo)
        for version, repo in (("1.4.0", "Example/quiz"), ("1.5.0", "Unrelated/quiz")):
            self.make_archives(version, repo)
            with self.assertRaises(ValueError):
                assets.assemble(self.dist, "v1.5.0", "Example/quiz", "unsigned")
        self.assertFalse((self.dist / "release.json").exists())
