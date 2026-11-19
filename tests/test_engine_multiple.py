import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from engine import LectureIndex, import_document, solve


class MultipleAnswerTests(unittest.TestCase):
    question = "Какие методы устойчивы к выбросам?"
    options = ["Медиана", "Среднее", "Квантили", "Дисперсия"]

    def bank(self, **fields):
        card = {"question": self.question, "options": self.options, **fields}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "methods.quiz.json"
            path.write_text(json.dumps({"cards": [card]}, ensure_ascii=False), encoding="utf-8")
            return LectureIndex(import_document(path))

    def test_bank_order_mode_and_source_per_choice(self):
        index = self.bank(answers=["Квантили", "Медиана"])
        with patch("engine._api") as complete:
            result = solve(self.question, self.options[::-1], index, "qwen", selection_mode="multiple", expected_count=2)
            self.assertEqual((result["choices"], result["choice"], result["kind"]), ([2, 4], None, "exact"))
            self.assertEqual([(i["choice"], i["quote"]) for i in result["evidence"]], [(2, "Квантили"), (4, "Медиана")])
            self.assertTrue(all(i["source"].endswith("карточка 1") for i in result["evidence"]))
            self.assertEqual(solve(self.question, self.options, index, "qwen")["choices"], [])
            self.assertEqual(solve(self.question, self.options, index, "qwen", selection_mode="multiple", expected_count=3)["choices"], [])
            self.assertEqual(solve("Какие из методов устойчивы к выбросам?", self.options, index, "qwen",
                                   selection_mode="multiple", expected_count=3)["choices"], [])
            self.assertEqual(solve(self.question, self.options[:3] + ["Другое"], index, "qwen", selection_mode="multiple")["choices"], [])
            complete.assert_not_called()

    def test_singleton_depends_on_card_mode(self):
        for fields, mode in (({"answer": "Медиана"}, "single"), ({"answers": ["Медиана"]}, "multiple")):
            index = self.bank(**fields)
            other = "multiple" if mode == "single" else "single"
            result = solve(self.question, self.options, index, selection_mode=mode)
            self.assertEqual((result["choices"], result["choice"]), ([1], 1))
            with patch("engine._api") as complete:
                self.assertEqual(solve(self.question, self.options, index, "qwen", selection_mode=other)["choices"], [])
                complete.assert_not_called()

    def test_invalid_bank_entries(self):
        for fields in ({}, {"answers": "Квантили"}, {"answers": []}, {"answers": [""]},
                       {"answers": ["Медиана", "медиана"]}, {"answers": ["Нет такого варианта"]},
                       {"answer": "Медиана", "answers": ["Медиана"]}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.bank(**fields)

    @staticmethod
    def lectures():
        return LectureIndex([
            {"source": "Лекция A", "location": "стр. 3", "text": "Медиана устойчива к выбросам."},
            {"source": "Лекция B", "location": "стр. 8", "text": "Квантили могут быть устойчивы к выбросам."},
        ])

    def test_one_call_can_return_independent_citations(self):
        def reply(_url, _path, payload, timeout):
            data = json.loads(payload["messages"][1]["content"])
            self.assertIn("ровно 2", payload["messages"][0]["content"])
            citations = {p["text"].split()[0]: p["id"] for p in data["fragments"]}
            result = {"selections": [{"choice": 3, "evidence": str(citations["Квантили"])},
                                     {"choice": 1, "evidence": citations["Медиана"]}]}
            return {"choices": [{"message": {"content": json.dumps(result)}}]}

        with patch("engine._api", side_effect=reply) as complete:
            result = solve(self.question, self.options, self.lectures(), "qwen", selection_mode="multiple", expected_count=2)
        complete.assert_called_once()
        self.assertEqual(result["choices"], [1, 3])
        self.assertEqual([i["source"] for i in result["evidence"]], ["Лекция A · стр. 3", "Лекция B · стр. 8"])

    def test_reject_partial_invalid_or_missing_confirmation(self):
        valid = {"choice": 1, "evidence": 1}
        for selection, count in (([valid, valid], None), ([valid, {"choice": 3, "evidence": 99}], 2),
                                 ([valid, {"choice": 3}], None), ([valid, {"choice": 5, "evidence": 2}], None),
                                 ([valid], 2), ([{"choice": True, "evidence": 1}], 1),
                                 ([{"choice": 1, "evidence": False}], None), ([], None)):
            with self.subTest(selection=selection, count=count):
                reply = {"choices": [{"message": {"content": json.dumps({"selections": selection})}}]}
                with patch("engine._api", return_value=reply):
                    result = solve(self.question, self.options, self.lectures(), "qwen", selection_mode="multiple", expected_count=count)
                self.assertEqual((result["choices"], result["choice"], result["evidence"]), ([], None, []))

    def test_two_choices_can_share_one_passage(self):
        text = "Медиана и квантили могут быть устойчивы к выбросам."
        index = LectureIndex([{"source": "Лекция", "location": "стр. 5", "text": text}])
        selections = [{"choice": 1, "evidence": 1}, {"choice": 3, "evidence": 1}]
        reply = {"choices": [{"message": {"content": json.dumps({"selections": selections})}}]}
        with patch("engine._api", return_value=reply):
            result = solve(self.question, self.options, index, "qwen", selection_mode="multiple")
        self.assertEqual(result["choices"], [1, 3])
        self.assertEqual([item["quote"] for item in result["evidence"]], [text, text])

    def test_modes_and_count_bounds(self):
        for mode, count in (("both", None), ("multiple", True), ("multiple", 0), ("single", 2), ("multiple", 5)):
            with self.subTest(mode=mode, count=count), self.assertRaises(ValueError):
                solve(self.question, self.options, self.lectures(), selection_mode=mode, expected_count=count)
