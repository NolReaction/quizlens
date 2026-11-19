import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_mac", ROOT / "packaging/package_mac.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class MacReleaseTests(unittest.TestCase):
    def test_signing_requires_mac_developer_id_and_profile(self):
        with patch.object(release.sys, "platform", "linux"):
            with self.assertRaises(ValueError):
                release.signing_settings(True, {})
        variables = {"QUIZLENS_SIGN_IDENTITY": "Developer ID Application: Test (ABCDE)",
                     "QUIZLENS_NOTARY_PROFILE": "notary", "QUIZLENS_KEYCHAIN": "/tmp/example.keychain-db"}
        with patch.object(release.sys, "platform", "darwin"):
            for change in ({"QUIZLENS_SIGN_IDENTITY": "-"}, {"QUIZLENS_NOTARY_PROFILE": ""}):
                with self.assertRaises(ValueError):
                    release.signing_settings(True, {**variables, **change})
            options = release.signing_settings(True, variables)
            self.assertEqual(options[-1], ["--keychain", variables["QUIZLENS_KEYCHAIN"]])
            self.assertIsNone(release.signing_settings(False, variables))

    def test_sign_helpers_before_bundle_using_hardened_runtime(self):
        app = Path("/example/QuizLens.app")
        with patch.object(release, "run") as command:
            release.sign_app(app, ("Developer ID Application: Example", "profile", ["--keychain", "/path"]))
        calls = [entry.args for entry in command.call_args_list]
        self.assertEqual([args[-1] for args in calls[:-1]],
                         [*(app / "Contents/Helpers" / n for n in release.HELPERS), app])
        for args in calls[:-1]:
            self.assertIn("--timestamp", args)
            self.assertIn("runtime", args)
            self.assertIn("--keychain", args)
        self.assertIn("--strict", calls[-1])

    def test_staple_only_after_explicit_acceptance(self):
        options = ("identifier", "profile", [])
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            for status, rc in (("Invalid", 0), ("In Progress", 0), ("Accepted", 1), (None, 0)):
                receipt = SimpleNamespace(returncode=rc, stdout=json.dumps({"status": status}))
                with patch.object(release, "ditto_archive"), patch.object(release, "run") as commands, \
                     patch.object(release.subprocess, "run", return_value=receipt):
                    with self.assertRaises(RuntimeError):
                        release.notarize(work / "QuizLens.app", work, options)
                    commands.assert_not_called()
            receipt = SimpleNamespace(returncode=0, stdout='{"status":"Accepted","id":"test"}')
            with patch.object(release, "ditto_archive"), patch.object(release, "run") as commands, \
                 patch.object(release.subprocess, "run", return_value=receipt) as submission:
                release.notarize(work / "QuizLens.app", work, options)
            self.assertIn("--wait", submission.call_args.args[0])
            self.assertEqual([call.args[1] for call in commands.call_args_list],
                             ["stapler", "--verify", "stapler", "--assess"])

    def test_copy_release_metadata_and_public_inputs(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "bundle"
            release.copy_inputs(ROOT, output)
            self.assertEqual({name.name for name in (output / "examples").iterdir()}, release.PUBLIC_EXAMPLES)
            self.assertEqual((output / "packaging/REPOSITORY").exists(), (ROOT / "packaging/REPOSITORY").exists())
            self.assertFalse((output / "packaging/PRECOMPILED").exists())
