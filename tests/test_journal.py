import concurrent.futures
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from journal import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.journal = Journal(self.path)
        self.question = "Отметь два формата."
        self.options = ["XML", "изображение", "JSON", "звук"]
        self.lectures = ["lecture-a", "lecture-b"]

    def record(self, options=None, question=None, mode="multiple", count=2):
        return self.journal.record(question or self.question, options or self.options, self.lectures,
                                   "local-qwen", mode, count, {"choices": [], "kind": "error", "detail": "Timeout",
                                   "source": "Лекция", "excerpt": "XML", "evidence": []}, "browser", 17.8, 9)

    def lookup(self, options=None, question=None, ids=None, mode="multiple", count=2):
        return self.journal.lookup(question or self.question, options or self.options,
                                   ids or self.lectures, mode, count)

    def test_history_and_card_persist_independently(self):
        ident = self.record()
        self.assertEqual(len(self.journal.list()), 1)
        before = self.journal.get(ident)
        self.assertEqual((before["captureMs"], before["solveMs"]), (18, 9))
        self.assertEqual(before["response"]["detail"], "Timeout")
        self.assertEqual(before["correctionChoices"], [])
        saved = self.journal.save_correction(ident, [3, 1])
        self.assertEqual(saved["correctionTexts"], ["XML", "JSON"])
        self.assertIsNone(self.lookup(question="Отметь два неверных формата."))
        self.assertIsNone(self.lookup(ids=["lecture-a"]))
        self.assertIsNone(self.lookup(ids=["lecture-c", "lecture-b"]))
        self.assertIsNone(self.lookup(mode="single", count=1))
        self.assertIsNone(self.lookup(count=3))
        self.assertIsNone(self.lookup(options=["XML", "изображение", "HTML", "звук"]))
        self.assertIsNone(self.lookup(options=["XML", "изображение", "json", "звук"]))
        self.assertTrue(self.journal.list()[0]["corrected"])
        self.journal = Journal(self.path)
        reordered = list(reversed(self.options))
        found = self.lookup(options=reordered, ids=self.lectures[::-1])
        self.assertEqual(found["kind"], "correction")
        self.assertEqual(found["choices"], [2, 4])
        self.assertEqual([item["choice"] for item in found["evidence"]], [2, 4])
        self.assertEqual([item["quote"] for item in found["evidence"]], ["JSON", "XML"])
        self.assertEqual(self.journal.delete(ident), {"deleted":True})
        self.assertEqual(self.journal.list(), [])
        self.assertEqual(self.lookup(options=reordered)["choices"], [2, 4])
        with self.assertRaises(ValueError):
            self.journal.get(ident)

    def test_repeated_corrections_and_forget(self):
        first = self.record()
        self.journal.save_correction(first, [1, 3])
        second = self.record(self.options[::-1])
        self.assertEqual(self.journal.get(second)["correctionChoices"], [2, 4])
        self.journal.save_correction(second, [1, 2])
        self.assertEqual(self.journal.get(first)["correctionChoices"], [3, 4])
        self.assertEqual(self.lookup()["choices"], [3, 4])
        self.assertEqual(self.journal.forget(first)["correctionChoices"], [])
        self.assertIsNone(self.lookup())
        self.assertFalse(any(entry["corrected"] for entry in self.journal.list()))

    def test_validates_complete_distinct_answers(self):
        ident = self.record()
        for value in [None, 1, [], [0, 1], [True, 3], [2, 2], [1, 3, 4], [1], [1, 5]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.journal.save_correction(ident, value)
        duplicate = self.record(["XML", " XML ", "HTML", "JSON"])
        with self.assertRaises(ValueError):
            self.journal.save_correction(duplicate, [1, 2])
        single = self.record(mode="single", count=None)
        with self.assertRaises(ValueError):
            self.journal.save_correction(single, [2, 3])
        self.journal.save_correction(single, [3])
        self.assertEqual(self.lookup(mode="single", count=1)["choice"], 3)

    def test_concurrent_retention_and_reopen(self):
        self.journal.HISTORY_LIMIT = 7
        self.journal.CARD_LIMIT = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(lambda i:self.record(question=f"Вопрос {i}"), range(30)))
        rows = self.journal.list()
        self.assertEqual(len(rows), 7)
        self.assertEqual(len(set(x["id"] for x in rows)), 7)
        for row in rows:
            self.journal.save_correction(row["id"], [1, 3])
        self.assertEqual(sum(x["corrected"] for x in self.journal.list()), 4)
        self.journal = Journal(self.path)
        self.assertEqual(len(self.journal.list()), 7)
        self.assertEqual(sum(x["corrected"] for x in self.journal.list()), 4)

    def test_trace_is_bounded_ordered_and_handles_old_history(self):
        def frame(age, stem, status="complete"):
            return {"ageMs": age, "question": stem, "options": ["Один", "Два"], "status":status}
        trace = [frame(7000, "Слишком старый"), frame(True, "Плохое время"), frame(1,"Неверное","error"),
                 frame(2000, "Мерцающая цифра"), {"ageMs":75, "question":5, "options":[], "status":"partial"},
                 frame(1050, "Длинный " * 100, "unstable"),
                 {"ageMs":750, "question":" Нет ", "options":["a" * 500] * 11, "status":"complete"},
                 frame(0, "Текущий вопрос"), frame(45, " Текущий  вопрос  "),
                 {"ageMs":200, "question":"", "options":[], "status":"partial"}]
        ident = self.journal.record(self.question, self.options, self.lectures, "", "multiple", 2,
                                    {}, "native", None, 10, capture_trace=trace)
        frames = self.journal.get(ident)["captureTrace"]
        self.assertEqual([f["ageMs"] for f in frames], [1050,750,200,0])
        self.assertEqual(len(frames[0]["question"]), 500)
        self.assertEqual(len(frames[1]["options"]), 8)
        self.assertEqual(len(frames[1]["options"][0]), 350)
        self.assertEqual(frames[1]["question"], "Нет")
        self.assertEqual(frames[2]["options"], [])
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            body, = db.execute("SELECT body FROM captures WHERE id=?", (ident,)).fetchone()
            old = json.loads(body)
            del old["captureTrace"]
            db.execute("UPDATE captures SET body=? WHERE id=?", (json.dumps(old), ident))
        self.assertEqual(self.journal.get(ident)["captureTrace"], [])


if __name__ == "__main__":
    unittest.main()
