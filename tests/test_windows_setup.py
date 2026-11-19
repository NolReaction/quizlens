import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import ocr


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        ocr._tesseract_config.cache_clear()
        self.addCleanup(ocr._tesseract_config.cache_clear)

    def test_per_user_tesseract_does_not_require_path(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "Programs" / "Tesseract-OCR" / "tesseract.exe"
            binary.parent.mkdir(parents=True)
            binary.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}, clear=True), patch.object(ocr.sys, "platform", "win32"):
                with patch.object(ocr.shutil, "which", return_value=None):
                    self.assertEqual(ocr._tesseract_binary(), str(binary))
                    with patch.dict(os.environ, {"QUIZ_LENS_TESSERACT": str(binary) + ".missing"}):
                        with self.assertRaisesRegex(RuntimeError, "QUIZ_LENS_TESSERACT"):
                            ocr._tesseract_binary()

    def test_status_limited_language_and_missing_executable(self):
        with patch.dict("sys.modules", {"PIL": None}):
            self.assertIn("Install-OCR.cmd", ocr.windows_ocr_status()["detail"])
        if not self.has_pillow():
            self.skipTest("Pillow is optional")
        with patch.object(ocr, "_tesseract_config", return_value=("engine", "eng")):
            status = ocr.windows_ocr_status()
            self.assertEqual(status["state"], "partial")
            self.assertIn("русский (rus)", status["detail"])
        with patch.object(ocr, "_tesseract_config", side_effect=RuntimeError("Tesseract отсутствует")):
            self.assertEqual(ocr.windows_ocr_status()["state"], "failed")

    @staticmethod
    def has_pillow():
        try:
            from PIL import Image
        except ImportError:
            return False
        return True

    def test_windows_extracts_text_without_poppler(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        except ImportError:
            self.skipTest("Optional PDF package is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "my lecture.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=200)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 120 Td (Lecture on analytics) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with path.open("wb") as handle:
                writer.write(handle)
            with patch.object(engine.sys, "platform", "win32"), patch.object(engine.shutil, "which", return_value=None):
                result = list(engine._pdf_units(path))
            self.assertEqual(result[0][0], "стр. 1")
            self.assertIn("Lecture on analytics", result[0][1])
