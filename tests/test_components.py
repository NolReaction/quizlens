from pathlib import Path
import hashlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import components


class ComponentsTests(unittest.TestCase):
    def test_language_integrity_failure_leaves_active_pack_untouched(self):
        data = b"expected"
        info = {"eng": (len(data), hashlib.sha256(data).hexdigest())}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            runtime = Path(directory)
            with patch.object(components, "LANGUAGES", info), patch.object(components, "urlopen", return_value=io.BytesIO(data)):
                components.install_languages(runtime)
            active = Path(os.environ["TESSDATA_PREFIX"])
            self.assertTrue((active / "configs/tsv").is_file())
            with patch.object(components, "LANGUAGES", info), patch.object(components, "urlopen", return_value=io.BytesIO(b"tampered")):
                with self.assertRaisesRegex(RuntimeError, "Проверка"):
                    components.install_languages(runtime)
            self.assertEqual((active / "eng.traineddata").read_bytes(), data)

    def test_component_status_skips_install_when_already_ready(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(components, "pillow_ready", return_value=True):
            component = components.OCRComponents(Path(directory))
            component.platform = "windows"
            with patch.object(components.ocr, "_tesseract_config", return_value=("tesseract", "rus+eng")), patch.object(components.threading, "Thread") as start:
                self.assertEqual(component.install()["status"], "ready")
                start.assert_not_called()

    def test_repeated_install_requests_coalesce(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(components, "pillow_ready", return_value=False):
            component = components.OCRComponents(Path(directory))
            component.platform = "windows"
            with patch.object(components.ocr, "_tesseract_config", side_effect=RuntimeError), patch.object(components.threading, "Thread") as thread:
                self.assertEqual(component.install()["status"], "installing")
                self.assertEqual(component.install()["status"], "installing")
                thread.assert_called_once()

    def test_selected_folder_is_stable_across_app_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            source = Path(directory) / "release"
            source.mkdir()
            for name in components.EXTENSION_FILES:
                (source / name).write_text("first")
            folder = components.ExtensionFolder(runtime, source)
            folder.sync()
            (source / "reader.js").write_text("second")
            folder.sync()
            self.assertEqual((runtime / "browser-extension/reader.js").read_text(), "second")
            self.assertEqual((runtime / "browser-extension/manifest.json").read_text(), "first")


class PublicInstallationLinkTests(unittest.TestCase):
    def test_public_site_can_open_only_the_local_landing_page(self):
        from app import Handler
        from types import SimpleNamespace
        page = SimpleNamespace(server=SimpleNamespace(server_port=8765), path="/", command="GET",
                               headers={"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "cross-site",
                                        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        Handler.validate(page)
        for method, path in (("GET", "/api/update"), ("POST", "/"), ("POST", "/api/components/ocr")):
            page.command, page.path = method, path
            with self.assertRaises(PermissionError):
                Handler.validate(page)


class LoopbackStartupTests(unittest.TestCase):
    def test_bind_allocates_port_without_reverse_dns(self):
        from app import Handler, QuizServer
        with patch("socket.getfqdn", side_effect=AssertionError("Unexpected DNS lookup")):
            with QuizServer(("127.0.0.1", 0), Handler) as server:
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertEqual(server.server_port, server.socket.getsockname()[1])
                self.assertGreater(server.server_port, 0)
