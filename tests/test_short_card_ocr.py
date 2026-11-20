import unittest
from unittest.mock import patch

from ocr import _short_card_reading

try:
    from PIL import Image
except ImportError:
    Image = None


def word(text, x=0, width=10):
    return {"text": text, "confidence": 95, "x": x, "y": 30, "w": width, "h": 20}


@unittest.skipUnless(Image, "Pillow is required for the compact OCR strip")
class ShortCardOCRTests(unittest.TestCase):
    def setUp(self):
        self.pages = [Image.new("L", (50, 40), 255) for _ in range(4)]
        self.groups = [[word("Си")], [], [word("Ср")], [word("Со")]]
        self.latin = [word(text, 30 + 63 * i) for i, text in enumerate(("Cu", "C", "Cp", "Co"))]

    def read(self, question):
        return _short_card_reading(self.pages, self.groups, question, "tesseract", "rus+eng")

    def test_cyrillic_reading_retains_script_and_recovers_the_missing_choice(self):
        cyrillic = [word(text, 30 + 63 * i) for i, text in enumerate(("Си", "С", "Ср", "Со"))]
        with patch("ocr._read_tsv", return_value=[cyrillic]) as retry:
            groups = self.read("Какая нота отмечена?")
        self.assertEqual([group[0]["text"] for group in groups], ["Си", "С", "Ср", "Со"])
        self.assertEqual(retry.call_args.args[2], "rus+eng")

    def test_chemical_symbols_keep_their_case_without_changing_reliable_glyphs(self):
        with patch("ocr._read_tsv", return_value=[self.latin]) as retry:
            groups = self.read("Какой химический символ у меди?")
        self.assertEqual([group[0]["text"] for group in groups], ["Cu", "C", "Cp", "Co"])
        self.assertEqual(retry.call_args.args[2], "eng+rus")
        self.groups[2] = [word("10%")]
        with patch("ocr._read_tsv", return_value=[self.latin]):
            groups = self.read("Какой химический символ у меди?")
        self.assertEqual(groups[2][0]["text"], "10%")

    def test_merged_composite_words_are_rejected(self):
        self.latin[0]["w"] = 95
        with patch("ocr._read_tsv", return_value=[self.latin]):
            self.assertIs(self.read("Какой химический символ у меди?"), self.groups)
