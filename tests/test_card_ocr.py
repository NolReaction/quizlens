import unittest
from pathlib import Path
from unittest.mock import patch

from engine import LectureIndex, import_document, solve


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class OCRCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deck = import_document(EXAMPLES / "analytics-demo.quiz.json")
        cls.card = next(c for c in cls.deck if c["question"].startswith("Из 1000"))

    def test_read_accuracy_from_approved_card_without_calling_model(self):
        index = LectureIndex(self.deck)
        for typo in ["Ассигасу", "Acciracy", "Aсcuracy"]:
            for options, position in [(self.card["options"], 4), (self.card["options"][::-1], 1)]:
                with self.subTest(typo=typo, options=options), patch("engine._ask_model") as model:
                    question = self.card["question"].replace("Accuracy", typo)
                    result = solve(question, options, index, "test")
                    self.assertEqual((result["kind"], result["choice"]), ("ocr_card", position))
                    self.assertEqual(result["matchedQuestion"], self.card["question"])
                    self.assertEqual(result["quote"], "99 %")
                    model.assert_not_called()

    def test_preserve_numbers_polarity_and_unverified_choice_sets(self):
        index = LectureIndex(self.deck)
        question = self.card["question"].replace("Accuracy", "Ассигасу")
        options = self.card["options"]
        for changed in [question.replace("1000", "100"), question.replace("равна", "не равна"),
                        question.replace("ушли 10", "ушли < 10"), question.replace("Ассигасу", "Recall"),
                        question.replace("Ассигасу", "Acczzacy")]:
            self.assertIsNone(index.exact_answer(changed, options), changed)
        self.assertIsNone(index.exact_answer(question, ["0%", "2%", "50%", "99%"]))
        missing_options = {key: value for key, value in self.card.items() if key != "options"}
        self.assertIsNone(LectureIndex([missing_options]).exact_answer(question, options))
        self.assertEqual(index.exact_answer(self.card["question"], options)["kind"], "exact")

    def test_conflicting_cards_or_documented_spelling_do_not_guess(self):
        question = self.card["question"].replace("Accuracy", "Acciracy")
        index = LectureIndex([self.card, dict(self.card, answer="1 %")])
        self.assertIsNone(index.exact_answer(question, self.card["options"])["choice"])
        term = {"source": "author", "location": "1", "text": "Acciracy is an identifier."}
        self.assertIsNone(LectureIndex([self.card, term]).exact_answer(question, self.card["options"]))
        quoted = dict(self.card, question=self.card["question"].replace("Accuracy", "«Accuracy»"))
        self.assertIsNone(LectureIndex([quoted]).exact_answer(question.replace("Acciracy", "«Acciracy»"), self.card["options"]))

    def test_flag_correction_on_multiple_and_preserve_required_count(self):
        card = {**self.card, "question": "Which options describe Accuracy correctly?", "answers": ["B", "D"],
                "options": ["A", "B", "C", "D"]}
        del card["answer"]
        index = LectureIndex([card])
        question = card["question"].replace("Accuracy", "Acciracy")
        result = index.exact_answer(question, ["D", "C", "B", "A"], "multiple", 2)
        self.assertEqual((result["kind"], result["choices"]), ("ocr_card", [1, 3]))
        self.assertFalse(index.exact_answer(question, card["options"], "multiple", 1)["choices"])
        self.assertIsNone(index.exact_answer(question, card["options"], "single"))
