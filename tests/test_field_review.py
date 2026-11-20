import base64
import io
import unittest
from unittest.mock import Mock, patch

import ocr
import ocr_review as review

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None


def word(text, confidence=91, x=10, y=8, w=45, h=16):
    return {"text": text, "confidence": confidence, "x": x, "y": y, "w": w, "h": h}


class FieldGeometry(unittest.TestCase):
    def test_ambiguous_overlapping_fields_are_never_assigned(self):
        box = {"x": .4, "y": .31, "w": .11, "h": .03}
        row = {"x": .05, "y": .27, "w": .84, "h": .09}
        self.assertEqual(review.field_for_box(box, [row]), 0)
        self.assertIsNone(review.field_for_box(box, [row, row]))
        self.assertIsNone(review.field_for_box(box, []))


@unittest.skipUnless(Image, "Pillow required")
class CriticalReadingTests(unittest.TestCase):
    def setUp(self):
        self.page = Image.new("L", (190, 43), "white")
        self.words = [word("+125", 38)]
        self.texts = ["Как изменилось значение?", "+125", "5", "24"]
        self.pages = [self.page] * len(self.texts)
        self.groups = [[], self.words, [], []]

    def read(self, callback):
        return review.field_warnings(self.pages, self.pages, self.groups, self.texts, None, callback)

    def test_disagreement_preserves_primary_and_sends_only_a_bounded_crop(self):
        out = self.read(lambda imgs: [[word("+12,5")], [word("+12.5")]])
        self.assertEqual([(row["field"], row["readings"]) for row in out], [(1, ["+125", "+12,5"])])
        self.assertEqual(self.texts[1], "+125")
        pixels = base64.b64decode(out[0]["image"].split(",")[1])
        self.assertLess(Image.open(io.BytesIO(pixels)).width, self.page.width)

    def test_confidence_missing_contradiction_or_discarded_ui_never_blocks(self):
        for readings in ([[], []], [[word("Арбуз")], [word("5")]], [[word("2", 5)], [word("2")]],
                         [[word("+12,5")], [word("−12,5")]]):
            self.assertEqual(self.read(lambda _, result=readings: result), [])
        with patch.object(review, "_views", side_effect=AssertionError):
            self.texts[1] = "50"
            self.assertEqual(self.read(Mock()), [])
            self.texts[1] = "105"
            self.groups[1] = [word("105", 99, h=30)]
            self.assertEqual(self.read(Mock()), [])
        self.texts[1] = "99 %"
        self.groups[1] = [word("99", 32)]
        self.assertEqual(self.read(lambda _: [[word("99%")], [word("99%")]]), [])

    def test_negation_and_typographic_variants(self):
        self.texts[0] = "Это но относится к расходам?"
        self.groups[:] = [[word("но", 41)], [], [], []]
        self.assertEqual(self.read(lambda _: [[word("не")], [word("НЕ")]])[0]["readings"], ["но", "не"])
        for first, repeated in (("0,5%", "0.5%"), ("—12%", "−12%"), ("<=", "≤"), ("не", "НЕ")):
            self.texts[0], self.groups[0] = first, [word(first, 25)]
            self.assertEqual(self.read(lambda _: [[word(repeated)], [word(repeated)]]), [])

    def test_comparison_symbol_is_read_from_same_pixels(self):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 26)
        except OSError:
            self.skipTest("Test font unavailable")
        for value in ("≥", "≤", "=", ">", "<", "2"):
            phrase = "x " + value + " 12"
            image = Image.new("L", (120, 44), "black")
            ImageDraw.Draw(image).text((6, 2), phrase, fill="white", font=font)
            self.assertEqual(review._comparison_glyph(image), None if value == "2" else value)

    def test_one_bounded_process_and_safe_timeout(self):
        self.texts = [str(i) + "%" for i in range(9)]
        self.pages = [self.page] * len(self.texts)
        self.groups = [[word(s, confidence=65)] for s in self.texts]
        callback = Mock(side_effect=RuntimeError("time limit"))
        self.assertEqual(self.read(callback), [])
        callback.assert_called_once()
        self.assertLessEqual(len(callback.call_args.args[0]), 8)

    def test_real_tesseract_confirms_an_independent_decimal_reading(self):
        try:
            binary, languages = ocr._tesseract_config()
            if "eng" not in languages.split("+"):
                self.skipTest("English OCR data unavailable")
            font = ImageFont.truetype("DejaVuSans.ttf", 27)
        except (OSError, RuntimeError):
            self.skipTest("Needs Tesseract and DejaVu Sans")
        image = Image.new("L", (170, 48), 246)
        draw = ImageDraw.Draw(image)
        draw.text((12, 2), "+12,5", font=font, fill=10)
        l, t, r, b = draw.textbbox((12, 2), "+12,5", font=font)
        self.groups[1] = [word("+125", 51, x=l, y=t, w=r-l, h=b-t)]
        self.pages[1] = image
        readings = self.read(lambda imgs: ocr._read_tsv(imgs, binary, "eng", "7", 3))
        self.assertEqual(readings[0]["readings"], ["+125", "+12,5"])
