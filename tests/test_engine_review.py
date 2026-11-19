import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from engine import LectureIndex, explain_option, import_document, solve


def completion(data):
    return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}


def import_cards(cards):
    with TemporaryDirectory() as folder:
        path = Path(folder) / "test.quiz.json"
        path.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")
        return LectureIndex(import_document(path))


class VariableOptionTests(unittest.TestCase):
    def test_all_supported_sizes_and_reordering(self):
        for size in range(2, 9):
            options = [f"Значение {i}" for i in range(1, size + 1)]
            for mode, key, expected in (("single", {"answer": options[0]}, [size]),
                                        ("multiple", {"answers": options[::2]}, sorted(size - i for i in range(0, size, 2)))):
                with self.subTest(size=size, mode=mode):
                    bank = import_cards([{"question": "Какая группа?", "options": options, **key}])
                    res = solve("Какая группа?", options[::-1], bank, selection_mode=mode, expected_count=len(expected))
                    self.assertEqual(res["choices"], expected)
                    self.assertEqual([e["choice"] for e in res["evidence"]], expected)
        for size in (0, 1, 9):
            opts = [str(i + 1) for i in range(size)]
            with self.assertRaises(ValueError):
                solve("Сколько?", opts, LectureIndex([]))
            with self.assertRaises(ValueError):
                import_cards([{"question": "Сколько?", "options": opts, "answer": "1"}])

    def test_model_bound_matches_actual_option_count(self):
        index = LectureIndex([{"text": "Форматы зависят от источника.", "source": "L", "location": "1"}])
        q = "Какой формат зависит от источника?"
        for size in (2, 5, 8):
            options = [str(i) for i in range(size)]
            with patch("engine._api", return_value=completion({"choice": size, "evidence": 1})):
                self.assertEqual(solve(q, options, index, "llm")["choice"], size)
            with patch("engine._api", return_value=completion({"choice": size + 1, "evidence": 1})):
                self.assertFalse(solve(q, options, index, "llm")["choices"])
            selected = [{"choice": i + 1, "evidence": 1} for i in range(size)]
            with patch("engine._api", return_value=completion({"selections": selected})):
                self.assertEqual(solve(q, options, index, "llm", selection_mode="multiple", expected_count=size)["choices"],
                                 list(range(1, size + 1)))


class ExplainOptionTests(unittest.TestCase):
    question = "Какая метрика учитывает пропуски?"
    options = ["Precision", "Recall", "Accuracy"]
    excerpt = "Recall учитывает пропуски положительных объектов."

    def setUp(self):
        self.index = LectureIndex([{"text": self.excerpt, "source": "Метрики", "location": "слайд 8"}])

    def test_judgment_tied_to_option_and_original_source(self):
        def respond(_url, _route, payload, timeout):
            sent = json.loads(payload["messages"][1]["content"])
            self.assertEqual(sent["options"]["2"], "Recall")
            self.assertEqual(sent["targetOption"], 2)
            self.assertEqual(sent["passages"][0]["text"], self.excerpt)
            return completion({"option": "2", "verdict": "supports", "evidence": 1,
                               "explanation": "Именно Recall учитывает пропуски среди положительных примеров."})

        with patch("engine._api", side_effect=respond):
            res = explain_option(self.question, self.options, self.index, "local", 2)
        self.assertEqual((res["verdict"], res["source"], res["quote"]), ("supports", "Метрики · слайд 8", self.excerpt))

    def test_wrong_option_bogus_evidence_and_generic_reasons(self):
        good = {"option": 2, "verdict": "supports", "evidence": 1, "explanation": self.excerpt}
        for changes in ({"option": 1}, {"option": True}, {"evidence": 10}, {"evidence": False},
                        {"verdict": "maybe"}, {"explanation": "Recall — это верный ответ."}, {"explanation": "Recall"},
                        {"explanation": ""}, {"explanation": "Локальный вторник наступает утром."}):
            with self.subTest(changes=changes), patch("engine._api", return_value=completion({**good, **changes})):
                self.assertEqual(explain_option(self.question, self.options, self.index, "local", 2)["verdict"], "insufficient")
        for invalid in (0, 4, True, "2"):
            with self.assertRaises(ValueError):
                explain_option(self.question, self.options, self.index, "local", invalid)

    def test_offline_source_and_authored_explanation(self):
        with patch("engine._api") as model:
            unavailable = explain_option(self.question, self.options, self.index, "", 2)
            self.assertEqual((unavailable["verdict"], unavailable["quote"]), ("insufficient", self.excerpt))
            bank = import_cards([{"question": self.question, "options": self.options, "answer": "Recall",
                                  "explanation": self.excerpt}])
            for target, verdict in ((3, "supports"), (1, "contradicts")):
                reordered = ["Accuracy", "Precision", "Recall"]
                res = explain_option(self.question, reordered, bank, "", target)
                self.assertEqual((res["option"], res["verdict"], res["quote"]), (target, verdict, self.excerpt))
            model.assert_not_called()

    def test_no_conclusion_from_other_solved_card_or_from_outage(self):
        bank = import_cards([{"question": "Выберите худшую метрику?", "options": self.options, "answer": "Recall"}])
        with patch("engine._api") as model:
            self.assertEqual(explain_option(self.question, self.options, bank, "local", 2)["verdict"], "insufficient")
            model.assert_not_called()
        with patch("engine._api", side_effect=RuntimeError("Время ответа истекло.")):
            out = explain_option(self.question, self.options, self.index, "local", 2)
            self.assertEqual((out["verdict"], out["explanation"], out["quote"]), ("insufficient", "Время ответа истекло.", self.excerpt))
