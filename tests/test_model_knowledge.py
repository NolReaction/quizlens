import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import app
import engine
from journal import Journal


def completion(content):
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 143, "completion_tokens": 8, "total_tokens": 151}}


class ModelKnowledgeTests(unittest.TestCase):
    question = ("Какая ароматная многолетняя трава родом из Европы, Северной Африки и Азии "
                "особенно популярна в средиземноморской кухне?")
    options = ["Перец", "Мак", "Тимьян", "Паприка"]

    def setUp(self):
        self.server = object.__new__(app.QuizServer)
        self.server.corpus = Mock()
        self.server.meaning = Mock()
        self.server.journal = Mock()
        self.request = {"question": self.question, "options": self.options, "model": "qwen2.5-7b-instruct",
                        "withoutDocuments": True, "lectureIds": [], "channel": "browser", "record": False}

    def answer(self, *choices):
        return completion(json.dumps({"choices": [{"id": i, "text": self.options[i - 1]} for i in choices]}))

    def test_local_model_answers_captured_herb_question_without_citations(self):
        endpoint = "http://127.0.0.1:2345/v1"
        with patch.object(app, "ENDPOINT", endpoint), \
                patch("engine._api", return_value=self.answer(3)) as model:
            result = self.server.solve(self.request)
        self.assertEqual(result, {
            "selectionMode": "single", "expectedCount": 1, "kind": "model_knowledge", "choice": 3, "choices": [3],
            "source": "", "excerpt": "", "evidence": [], "detail": "",
            "tokenUsage": {"source": "model", "input": 143, "output": 8,
                           "inputEstimated": False, "outputEstimated": False},
        })
        model.assert_called_once()
        base_url, path, payload = model.call_args.args
        self.assertEqual((base_url, path, payload["model"]), (endpoint, "chat/completions", self.request["model"]))
        self.assertIn("по своим знаниям", payload["messages"][0]["content"])
        self.assertEqual(json.loads(payload["messages"][1]["content"]), {
            "question": self.question, "options": [{"id": i, "text": option} for i, option in enumerate(self.options, 1)],
            "selection_mode": "single", "expected_answer_count": 1,
        })
        self.assertEqual(self.server.corpus.mock_calls, [])
        self.assertEqual(self.server.meaning.mock_calls, [])
        self.assertEqual(self.server.journal.mock_calls, [])

    def test_multiple_values_are_sorted_and_checked_as_a_set(self):
        for count in (2, None):
            with self.subTest(count=count), patch("engine._api", return_value=self.answer(4, 1)):
                result = self.server.solve({**self.request, "selectionMode": "multiple", "expectedCount": count})
                self.assertEqual((result["selectionMode"], result["expectedCount"], result["choice"], result["choices"]),
                                 ("multiple", count, None, [1, 4]))

    def test_abstention_invalid_structure_choices_and_counts(self):
        for content in ('{"choices":[]}', 'bad result', '{"choices":[0]}', '{"choices":[5]}',
                        '{"choices":[true]}', '{"choices":[3.0]}', '{"choices":["3.0"]}', '{"choices":[3,"03"]}',
                        '{"choices":[3,4]}', '{"choice":3}', '{"choices":3}',
                        '{"choices":[{"id":3,"text":"Перец"}]}',
                        '{"choices":[{"id":5,"text":"Тимьян"}]}',
                        '{"choices":[{"id":3,"text":"Тимьян"},{"id":3,"text":"Тимьян"}]}'):
            with self.subTest(content=content), patch("engine._api", return_value=completion(content)) as model:
                result = self.server.solve(self.request)
                self.assertEqual((result["kind"], result["choice"], result["choices"], result["evidence"]),
                                 ("model_knowledge", None, [], []))
                self.assertTrue(result["detail"])
                self.assertEqual(result["tokenUsage"]["input"], 143)
                model.assert_called_once()
        with patch("engine._api", return_value=self.answer(3)):
            result = self.server.solve({**self.request, "selectionMode": "multiple", "expectedCount": 2})
        self.assertFalse(result["choices"])

    def test_model_suggestions_are_reviewable_only_if_history_is_enabled(self):
        with TemporaryDirectory() as folder, patch("engine._api", return_value=self.answer(3)):
            self.server.journal = Journal(folder)
            disabled = self.server.solve(self.request)
            self.assertNotIn("journalId", disabled)
            self.assertEqual(self.server.journal.list(), [])
            enabled = self.server.solve({**self.request, "record": True, "lectureIds": None})
            row = self.server.journal.get(enabled["journalId"])
            self.assertEqual((row["lectureIds"], row["response"]["choices"]), ([], [3]))
            self.assertTrue(row["reviewable"])
            self.assertIsNone(row["review"])
            self.assertEqual(self.server.journal.save_correction(row["id"], [3])["review"], "correct")

    def test_incompatible_opt_in_and_conflicting_recognition_never_call_model(self):
        with patch("engine._api") as model:
            for invalid in ({"withoutDocuments": "false"}, {"lectureIds": ["document"]}, {"lectureIds": ""},
                            {"model": "  "}, {"channel": "training"}, {"channel": "check"}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    self.server.solve({**self.request, **invalid})
            conflict = self.server.solve({**self.request, "expectedCount": 2})
            self.assertEqual((conflict["selectionMode"], conflict["choices"]), ("unknown", []))
            duplicated = self.server.solve({**self.request, "options": ["4%", "4 %"]})
            self.assertEqual((duplicated["choices"], duplicated["tokenUsage"]), ([], {"source": "none"}))
        model.assert_not_called()
        self.assertEqual(self.server.corpus.mock_calls, [])

    def test_grounded_requests_stay_grounded_and_model_failure_is_reported(self):
        with TemporaryDirectory() as folder, patch("engine._api", side_effect=RuntimeError("LM Studio недоступен")) as model:
            self.server.corpus = app.Corpus(Path(folder))
            with self.assertRaisesRegex(ValueError, "Отметь хотя бы один документ"):
                self.server.solve({**self.request, "withoutDocuments": False})
            self.server.meaning.wrap.return_value = engine.LectureIndex([])
            self.server.embedding_model = ""
            ids = [source["id"] for source in self.server.corpus.list()]
            grounded = self.server.solve({**self.request, "withoutDocuments": False, "channel": "check", "lectureIds": ids})
            self.assertEqual((grounded["kind"], grounded["choices"]), ("none", []))
            model.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "LM Studio недоступен"):
                self.server.solve(self.request)
            model.assert_called_once()
        self.assertEqual(self.server.journal.mock_calls, [])
