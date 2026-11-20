"""Release metadata, ZIP guardrails and update state used by the desktop launchers."""
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import updater

WINDOWS = "QuizLens-Windows.zip"


def archive(version):
    stream = io.BytesIO()
    root = "QuizLens-Windows/app/"
    with zipfile.ZipFile(stream, "w") as out:
        for name, contents in (("app.py", "pass"), ("updater.py", "pass"), ("ui/index.html", "QuizLens"),
                               ("packaging/VERSION", version), ("packaging/REPOSITORY", updater.REPOSITORY)):
            out.writestr(root + name, contents)
    return stream.getvalue()


class DesktopUpdateTests(unittest.TestCase):
    def test_same_official_channel_and_digest_required(self):
        base = {"version": "2.1.0", "tag": "v2.1.0", "repository": updater.REPOSITORY, "sha256": {WINDOWS: "0" * 64}}
        for edit in ({"tag": "v2.0.0"}, {"repository": "other/quizlens"}, {"sha256": {}}, {"version": "../2.1.0"}):
            with self.subTest(edit=edit), patch.object(updater, "fetch_official", return_value=json.dumps({**base, **edit})):
                with self.assertRaises(ValueError):
                    updater.latest_release(WINDOWS)
        with patch.object(updater, "fetch_official", return_value=json.dumps(base)):
            self.assertEqual(updater.latest_release(WINDOWS), ("2.1.0", "0" * 64))
        for url in ("http://github.com/test", "https://example.com", "https://github.com@example.com/x"):
            with self.assertRaises(ValueError):
                updater.official_url(url)

    def test_reject_tar_style_links_duplicates_and_unsafe_windows_names(self):
        for names in (["../outside"], ["QuizLens-Windows/app/file.py:stream"], ["QuizLens-Windows/AUX.txt"],
                      ["QuizLens-Windows/App.py", "QuizLens-Windows/app.py"], ["QuizLens-Windows/one./two"]):
            with self.subTest(names=names), zipfile.ZipFile(io.BytesIO(), "w") as bundle:
                for name in names:
                    bundle.writestr(name, "")
                with self.assertRaises(ValueError):
                    updater.validate_entries(bundle, "QuizLens-Windows")
        with zipfile.ZipFile(io.BytesIO(), "w") as bundle:
            link = zipfile.ZipInfo("QuizLens-Windows/app/shortcut")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "outside")
            with self.assertRaises(ValueError):
                updater.validate_entries(bundle, "QuizLens-Windows")

    def test_activation_waits_for_writes_and_background_work(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", QUIZLENS_UPDATE_SESSION=str(Path(tmp) / "session")), \
                patch.object(updater, "platform_asset", return_value=WINDOWS):
            closed = threading.Event()
            busy = [False]
            updates = updater.Updates("1.0.0", closed.set, lambda: busy[0])
            updates.state = "ready"
            updates.latest, updates.digest = "1.1.0", "0" * 64
            with updates.work():
                self.assertEqual(updates.command({"action": "activate"})["state"], "ready")
            busy[0] = True
            self.assertEqual(updates.command({"action": "activate"})["state"], "ready")
            self.assertFalse(closed.is_set())
            busy[0] = False
            self.assertEqual(updates.command({"action": "activate"})["state"], "restarting")
            self.assertTrue(closed.wait(1))
            with self.assertRaises(RuntimeError), updates.work():
                pass

    def test_competing_installer_can_finish_during_lock_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            entered = threading.Event()
            def install():
                with updater.install_lock(Path(tmp)):
                    entered.set()
                    time.sleep(.25)
            worker = threading.Thread(target=install)
            worker.start()
            self.assertTrue(entered.wait(2))
            with updater.install_lock(Path(tmp)):
                self.assertTrue(entered.is_set())
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_transaction_preserves_prior_version_on_bad_archive(self):
        old, new = "2.0.0", "2.0.1"
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            body = archive(old)
            with patch.object(updater, "fetch_official", return_value=body):
                updater.stage_release(cache, old, hashlib.sha256(body).hexdigest(), WINDOWS)
                updater.stage_release(cache, old, hashlib.sha256(body).hexdigest(), WINDOWS)
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    updater.stage_release(cache, new, "0" * 64, WINDOWS)
                with self.assertRaises(ValueError):
                    updater.stage_release(cache, new, hashlib.sha256(body).hexdigest(), WINDOWS)
            self.assertEqual([x.name for x in (cache / "versions").iterdir()], [old])
            self.assertTrue(updater.target_root(cache, old, WINDOWS).is_dir())
            self.assertIsNone(updater.target_root(cache, "../unsafe", WINDOWS))
