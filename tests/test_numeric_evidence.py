import unittest
from pathlib import Path
from unittest.mock import patch

from engine import LectureIndex, import_document, solve
from numeric_evidence import question_ratio


ACCURACY = "Из 1000 клиентов ушли 10. Модель предсказала всем клиентам «останется». Чему равна Accuracy?"
CONVERSION = "Магазин посетили 200 человек. Покупку сделали 8 человек. Какова конверсия посетителей в покупателей?"
OPTIONS = ["0%", "1%", "50%", "99%"]


def lecture(*texts):
    return LectureIndex([{"source": f"lecture-{i}.txt", "location": "1", "text": text}
                         for i, text in enumerate(texts)])


class NumericEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = LectureIndex(import_document(Path(__file__).resolve().parents[1] / "examples" / "analytics-demo.txt"))

    def test_wrong_model_and_order_do_not_affect_grounded_accuracy(self):
        with patch("engine._ask_model", return_value={"choices": [{"message": {"content": '{"choice":2,"evidence":1}'}}]}) as model:
            for model_name in ("test", ""):
                for options in (OPTIONS, ["99 %", "0%", "50%", "1%"]):
                    result = solve(ACCURACY, options, self.index, model_name)
                    self.assertEqual(options[result["choice"] - 1].replace(" ", ""), "99%")
                    self.assertEqual(result["kind"], "calculation")
                    self.assertIn("990 / 1000", result["verification"]["equation"])
                    self.assertIn("Accuracy", result["quote"])
            model.assert_not_called()

    def test_ocr_inconsistent_choices_and_unreadable_metric(self):
        with patch("engine._ask_model") as model:
            for question, options in ((ACCURACY, ["0%", "1%", "50%", "98%"]),
                                      (ACCURACY, ["0%", "99,0%", "50%", "99%"]),
                                      (ACCURACY.replace("Accuracy", "Ассигасу"), OPTIONS),
                                      (CONVERSION, ["2%", "24%", "8%", "16%"])):
                with self.subTest(question=question, options=options):
                    result = solve(question, options, self.index, "test")
                    self.assertIsNone(result["choice"])
                    self.assertTrue(result["detail"])
            model.assert_not_called()

    def test_real_low_and_decimal_rates_and_predictions_of_opposite_class(self):
        for question, options, correct in (
            (ACCURACY.replace("«останется»", "«уйдет»"), OPTIONS, 2),
            (ACCURACY.replace("ушли 10", "остались 10"), OPTIONS, 2),
            (CONVERSION.replace("200", "100").replace(" 8 ", " 24 "), ["2%", "24%", "8%", "16%"], 2),
            ("Из 8 прогнозов 3 оказались верными. Рассчитайте Accuracy.", ["0%", "37,5%", "3,75%", "62,5%"], 2),
            ("FP = 10, FN = 20, TP = 30. Чему равен Precision?", ["60%", "75%", "30%"], 2),
            ("FP = 10, FN = 20, TP = 30. Чему равен Recall?", ["60%", "75%", "30%"], 1),
            (CONVERSION, ["2%", "24%", "4,00%", "8%"], 3),
        ):
            with self.subTest(question=question):
                self.assertEqual(solve(question, options, self.index)["choice"], correct)

    def test_inconsistent_evidence_wins_over_valid_example_regardless_of_order(self):
        correct = "Accuracy = 990 / 1000 = 99 %."
        wrong = "Accuracy = 990 / 1000 = 1 %."
        for docs in ((correct, wrong), (wrong, correct)):
            with self.subTest(docs=docs), patch("engine._ask_model") as model:
                result = solve(ACCURACY, OPTIONS, lecture(*docs), "test")
                self.assertIsNone(result["choice"])
                self.assertEqual(result["quote"], wrong)
                model.assert_not_called()

    def test_other_metrics_or_operand_sets_do_not_support_a_calculation(self):
        index = lecture("Precision = 990 / 1000 = 99 %. Accuracy = 980 / 1000 = 98 %.")
        self.assertIsNone(solve(ACCURACY, OPTIONS, index)["choice"])
        # Even a real excerpt cannot justify a 1% answer the arithmetic contradicts.
        with patch("engine._ask_model", return_value={"choices": [{"message": {"content": '{"choice":2,"evidence":1}'}}]}):
            self.assertIsNone(solve(ACCURACY, OPTIONS, index, "test")["choice"])

    def test_header_ocr_does_not_change_operands_or_drop_other_conditions(self):
        options = ["2%", "4%", "8%", "16%"]
        for prefix in ("Метрики в аналитике Вопрос 4 изб ", "Метрики в аналитике Один ответ У Русский ~ 20сен ",
                       "Вопрос 3 / 10 "):
            with self.subTest(prefix=prefix), patch("engine._ask_model") as model:
                result = solve(prefix + CONVERSION, options, self.index, "test")
                self.assertEqual((result["kind"], result["choice"]), ("calculation", 2))
                model.assert_not_called()
        for prefix in ("Считайте наоборот Вопрос 4 из 6 ", "Вопрос 4 из 6 Кроме 10 посетителей. "):
            self.assertIsNone(solve(prefix + CONVERSION, options, self.index)["choice"])

    def test_bad_imported_key_with_independent_evidence_abstains(self):
        bad_card = {"question": ACCURACY, "answer": "1%", "options": OPTIONS, "source": "key", "text": ACCURACY, "location": "1"}
        index = LectureIndex([bad_card, *self.index.sections])
        with patch("engine._ask_model") as model:
            result = solve(ACCURACY, OPTIONS, index, "test")
            self.assertIsNone(result["choice"])
            self.assertIn("карточки", result["detail"])
            self.assertIn("Accuracy", result["quote"])
            model.assert_not_called()

    def test_explicit_negative_requires_a_unique_different_percentage(self):
        question = ACCURACY.replace("Чему равна", "Чему НЕ равна")
        with patch("engine._ask_model") as model:
            self.assertIsNone(solve(question, OPTIONS, self.index, "test")["choice"])
            unique = solve(question, ["1%", "99%"], self.index, "test")
            self.assertEqual(unique["choice"], 1)
            self.assertEqual(unique["verification"]["equation"], "990 / 1000 × 100 = 99%")
            model.assert_not_called()

    def test_invalid_counts_and_modified_premises(self):
        for question in (ACCURACY.replace("всем клиентам", "не всем клиентам"),
                         ACCURACY.replace("всем клиентам", "всем VIP-клиентам"),
                         ACCURACY.replace(" 10.", " 1001."),
                         ACCURACY.replace(" 10.", " 1,0."),
                         ACCURACY.replace("1000", "0"),
                         "TP=25, TP=20, FN=10. Чему равен Recall?",
                         "TP=0, FP=0. Чему равен Precision?"):
            with self.subTest(question=question):
                self.assertIsNone(question_ratio(question))
