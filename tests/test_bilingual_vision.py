import unittest
from unittest.mock import patch

import ocr
from ocr_terms import OCRVocabulary
from ocr_layout import ocr_texts

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None


class VocabularyTests(unittest.TestCase):
    def test_candidates_are_document_backed_and_unambiguous(self):
        terms = OCRVocabulary([{"text": "Accuracy Precision Как считаются доли?", "options": ["Recall"]}])
        for damaged in ("Ассигасу", "Acciracy", "Accurаcy"):
            self.assertEqual(terms.candidate(damaged), "accuracy")
        self.assertEqual(terms.candidate("Rеcall"), "recall")
        for token in ("Recall", "4cсuracy", "1%", "F1", "Recall2", "реальный", "RecXYZ", "Precision"):
            self.assertIsNone(terms.candidate(token))
        # A word already present in the document is not a transcription error.
        terms = OCRVocabulary([{"text": "Accuracy Ассигасу"}])
        self.assertIsNone(terms.candidate("Ассигасу"))
        terms = OCRVocabulary([{"text": "Accoracy Accuracy"}])
        self.assertIsNone(terms.candidate("Acciracy"))


@unittest.skipUnless(Image, "Pillow required for Tesseract image processing")
class WordImageTests(unittest.TestCase):
    def test_exclamation_like_vertical_strokes_require_a_clean_independent_word(self):
        image = Image.new("L", (120, 60), "white")
        terms = OCRVocabulary([{"text": "Recall учитывает пропуски."}])
        for reread, confidence, expected in (("Recall", 90, "Recall"), ("Recall!", 90, "Веса!"),
                                              ("Recall", 30, "Веса!"), ("Real", 95, "Веса!")):
            words = [dict(text="Веса!", x=10, y=8, w=46, h=15)]
            with patch.object(ocr, "_read_tsv", return_value=[[{"text": reread, "confidence": confidence}]]):
                ocr._verify_lecture_words([image], [words], "tesseract", terms)
            self.assertEqual(words[0]["text"], expected)
        self.assertIsNone(OCRVocabulary([{"text": "Recall веса"}]).ending_candidate("Веса"))
        self.assertIsNone(OCRVocabulary([{"text": "Recall Decall"}]).ending_candidate("Веса"))

    def test_correction_needs_agreement_of_the_document_and_an_independent_reading(self):
        image = Image.new("L", (200, 120), "white")
        words = [dict(text="«Ассигасу?»", x=8, y=12, w=74, h=18),
                 dict(text="1%", x=42, y=64, w=33, h=12)]
        terms = OCRVocabulary([{"text": "Compute Accuracy"}])
        with patch.object(ocr, "_read_tsv", return_value=[[{"text": "Other"}]]) as read:
            ocr._verify_lecture_words([image], [words], "tesseract", terms)
            self.assertEqual(len(read.call_args.args[0]), 1)
            self.assertEqual(read.call_args.args[2:], ("eng", "8"))
            self.assertEqual(words[0]["text"], "«Ассигасу?»")
        with patch.object(ocr, "_read_tsv", side_effect=RuntimeError):
            ocr._verify_lecture_words([image], [words], "tesseract", terms)
            self.assertEqual(words[0]["text"], "«Ассигасу?»")
        with patch.object(ocr, "_read_tsv", return_value=[[{"text": "Accuracy?"}]]):
            ocr._verify_lecture_words([image], [words], "tesseract", terms)
            self.assertEqual([w["text"] for w in words], ["«Accuracy?»", "1%"])
        with patch.object(ocr, "_read_tsv") as read:
            ocr._verify_lecture_words([image], [words], "tesseract", terms)
            read.assert_not_called()

    def test_pixels_separate_gutter_badge_only_with_scale_and_spacing_evidence(self):
        for bg, fg in (("white", "black"), ("black", "white")):
            for prefix_height, right, split in ((7, 36, True), (14, 36, False), (7, 15, False)):
                with self.subTest(bg=bg, prefix_height=prefix_height, right=right):
                    page = Image.new("L", (110, 64), bg)
                    draw = ImageDraw.Draw(page)
                    top = 21 + (15 - prefix_height) // 2
                    draw.rectangle((10, top, 13, top + prefix_height - 1), fill=fg)
                    draw.rectangle((right, 21, right + 7, 35), fill=fg)
                    draw.rectangle((right + 11, 21, right + 17, 35), fill=fg)
                    word = dict(text="10%", x=10, y=21, w=right + 8, h=15)
                    result = ocr._separate_badge(page, word)
                    if split:
                        self.assertEqual([p["text"] for p in result], ["1", "0%"])
                        self.assertEqual((result[1]["x"], result[1]["y"]), (right, 21))
                    else:
                        self.assertEqual(result, [word])

    def test_percentage_lower_loop_is_removed_only_when_it_overlaps(self):
        def box(text, x=0, y=0, w=40, h=18):
            return dict(text=text, x=x, y=y, w=w, h=h)
        for noise in ("fo", "°"):
            for x, expected in ((36, "1%"), (60, "1% " + noise)):
                groups = [[box("Что?")], [box("0%")], [box("1%"), box(noise, x, 11, 8, 7)],
                          [box("50%")], [box("99%")]]
                self.assertEqual(ocr_texts(groups)[2], expected)


if __name__ == "__main__":
    unittest.main()
