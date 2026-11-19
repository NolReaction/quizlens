"""Regression tests for the macOS session loop using WindowReader frame records."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import LectureIndex
from native_mode import NativeMode


class NativeStabilityTests(unittest.TestCase):
    def setUp(self):
        index = LectureIndex([dict(source="API.txt", location="1", text="API получает данные.")])
        self.solve = Mock(return_value={"choice": 1})
        self.mode = NativeMode(self.solve, lambda ids: index)
        payload = dict(id=1, regions=[[0, i / 5, 1, .15] for i in range(5)],
                       model="model", lectureIds=["lecture"])
        with patch.object(self.mode, "_executable", return_value="WindowReader"), \
                patch("native_mode.subprocess.Popen"), patch("native_mode.Thread.start"):
            self.mode.post("/api/native/start", payload)
        self.session = self.mode._session
        self.question = "Как используют API?"
        self.options = ["Первый результат", "Результат 2", "x + 10", "x − 10"]
        self.addCleanup(self.mode.close)

    def frame(self, question, options=None, seconds=None):
        fields = [question, *(self.options if options is None else options)]
        lines = [dict(region=i, text=text, x=.12, y=.1 + i*.16, w=.45, h=.035)
                 for i, text in enumerate(fields) if text]
        if seconds is not None:
            lines[:0] = [
                dict(region=0, text="Вопрос 3 из 6", x=.12, y=.04, w=.12, h=.02),
                dict(region=0, text=f"{seconds} с", x=.64, y=.04, w=.03, h=.02),
            ]
        self.mode._handle_frame(self.session, lines)

    def settle(self):
        self.frame(self.question)
        self.frame(self.question)
        pending, self.mode._pending = self.mode._pending, None
        self.assertEqual(pending[2]["question"], self.question)
        self.session.update(state="answer", answer={"choice": 3})

    def test_ocr_variant_and_single_frame_dropout_preserve_current_answer(self):
        self.settle()
        revision = self.session["revision"]
        for question in ("Как используют АРІ?", "", self.question, "Шум", self.question):
            self.frame(question)
            self.assertEqual(self.session["answer"], {"choice": 3})
            self.assertEqual(self.session["revision"], revision)
            self.assertIsNone(self.mode._pending)
        self.frame("")
        self.frame("")
        self.assertEqual(self.session["state"], "waiting")
        self.assertIsNone(self.session["answer"])

    def test_countdown_at_top_of_question_does_not_revise_or_requeue(self):
        self.frame(self.question, seconds=18)
        self.frame(self.question, seconds=17)
        self.assertEqual(self.mode._pending[2]["question"], self.question)
        self.mode._pending = None
        self.session.update(state="answer", answer={"choice": 2})
        for seconds in range(16, -1, -1):
            self.frame(self.question, seconds=seconds)
            self.assertEqual(self.session["revision"], 1)
            self.assertEqual(self.session["answer"], {"choice": 2})
            self.assertIsNone(self.mode._pending)

    def test_confirmed_changes_to_negation_order_and_formula_start_new_solve(self):
        self.settle()
        variants = [
            ("Как НЕ используют API?", self.options),
            ("Как НЕ используют API?", self.options[::-1]),
            ("Как НЕ используют API?", ["x − 100", "x + 10", "Результат 2", "Первый результат"]),
        ]
        for question, options in variants:
            rev = self.session["revision"]
            self.frame(question, options)
            self.assertEqual(self.session["revision"], rev)
            self.frame(question, options)
            self.assertIsNone(self.session["answer"])
            self.assertEqual(self.session["revision"], rev + 1)
            self.assertEqual(self.mode._pending[2]["options"], options)
            self.session.update(state="answer", answer={"choice": 2})
            self.mode._pending = None

    def test_old_answer_expires_during_continuously_unreadable_transition(self):
        self.settle()
        revision = self.session["revision"]
        with patch("native_mode.time.monotonic", side_effect=[10, 10.4, 11.4]):
            for q in ["Фрагмент один", "Фрагмент два", "Фрагмент три"]:
                self.frame(q)
        self.assertIsNone(self.session["answer"])
        self.assertEqual(self.session["state"], "waiting")
        self.assertEqual(self.session["revision"], revision + 1)

    def test_manual_checkbox_set_displays_all_citations_and_reorders_on_new_frame(self):
        question = "Что можно узнать из метаданных?"
        options = ["☐ Автор", "☐ Дата создания", "☐ Коэффициент", "☐ Среднее"]
        evidence = [dict(choice=i, source="Лекция", excerpt=text) for i, text in
                    [(1, "Автор файла"), (2, "Дата создания файла")]]
        self.solve.return_value = dict(choice=None, choices=[1, 2], evidence=evidence)
        self.frame(question, options)
        self.frame(question, options)
        payload = self.mode._pending[2]
        self.assertEqual(payload["selectionMode"], "multiple")
        self.assertIsNone(payload["expectedCount"])
        self.assertEqual(payload["options"], ["Автор", "Дата создания", "Коэффициент", "Среднее"])
        threading.Thread(target=self.mode._solve_worker, daemon=True).start()
        deadline = time.monotonic() + 2
        while self.session["state"] != "answer" and time.monotonic() < deadline:
            time.sleep(.005)
        status = self.mode.get("/api/native/status", {"session": self.session["id"]})
        self.assertEqual(status["answer"]["choices"], [1, 2])
        self.assertEqual(status["answer"]["evidence"], evidence)
        self.assertEqual(status["message"], "")
        revision = status["revision"]
        self.frame(question, options[::-1])
        self.frame(question, options[::-1])
        self.assertGreater(self.session["revision"], revision)

    def test_late_result_stays_hidden_and_expires_on_confirmed_switch(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        def solve(payload):
            if payload["question"] == self.question:
                started.set()
                release.wait(timeout=2)
                return {"choice": 4}
            return {"choice": 2}
        self.solve.side_effect = solve
        threading.Thread(target=self.mode._solve_worker, daemon=True).start()
        self.frame(self.question)
        self.frame(self.question)
        self.assertTrue(started.wait(timeout=2))
        self.frame("Какая метрика верна?")
        release.set()
        def wait_until(predicate):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self.mode._condition:
                    if predicate():
                        return
                time.sleep(.005)
            self.fail("Worker did not reach expected state")
        wait_until(lambda: self.session["held"] is not None)
        self.assertIsNone(self.session["answer"])
        self.frame("Какая метрика верна?")
        wait_until(lambda: self.session["state"] == "answer")
        self.assertEqual(self.session["answer"], {"choice": 2})
        self.assertEqual(self.solve.call_count, 2)
