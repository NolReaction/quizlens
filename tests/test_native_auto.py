"""Native auto mode with the same observation records emitted by WindowReader."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import LectureIndex
from native_mode import NativeMode


def line(text, x, y, width, height=.027):
    return dict(region=0, text=text, x=x, y=y, w=width, h=height)


class NativeAutoTests(unittest.TestCase):
    def setUp(self):
        self.mode = NativeMode(lambda request: {"choice": 1}, lambda ids: LectureIndex([]))
        self.addCleanup(self.mode.close)
        with patch.object(self.mode, "_executable", return_value="WindowReader"), \
                patch("native_mode.subprocess.Popen") as launch, patch("native_mode.Thread.start"):
            result = self.mode.post("/api/native/start", dict(id=42, auto=True, lectureIds=["lecture"]))
            self.assertEqual(launch.call_args.args[0], ["WindowReader", "--watch", "42"])
        self.session = self.mode._session
        self.token = result["session"]

    def test_armed_lobby_grid_then_vertical_new_question(self):
        lobby = [line("В ожидании ведущего", .16, .2, .35)]
        for _ in range(3):
            self.mode._handle_frame(self.session, lobby, 1.6)
        self.assertEqual(self.session["state"], "waiting")
        self.assertIsNone(self.mode._pending)
        question = "Какой формат хранит вложенные объекты?"
        choices = ["JSON", "PNG", "MP3", "WAV"]
        grid = [line(question, .12, .30, .52, .04)]
        grid += [line(f"{i+1}. {value}", .12 + (i % 2)*.40, .43 + (i//2)*.11, .18)
                 for i, value in enumerate(choices)]
        for _ in range(2):
            self.mode._handle_frame(self.session, grid, 1.6)
        self.assertEqual(self.mode._pending[2]["options"], choices)
        self.assertEqual(self.session["question"], question)
        self.mode._pending = None
        self.session.update(state="answer", answer={"choice": 1})
        for t in range(19, 13, -1):
            self.mode._handle_frame(self.session, grid + [line(f"{t} с", .76, .12, .05)], 1.6)
        self.assertIsNone(self.mode._pending)
        self.assertEqual(self.session["revision"], 1)
        for _ in range(2):
            self.mode._handle_frame(self.session, lobby, 1.6)
        self.assertIsNone(self.session["answer"])
        second = "Какой формат хранит фотографию?"
        stack = [line(second, .08, .20, .64, .04)]
        stack += [line(f"{i+1}. {value}", .08, .31 + i*.1, .28) for i, value in enumerate(choices[::-1])]
        for _ in range(2):
            self.mode._handle_frame(self.session, stack, .85)
        self.assertEqual(self.mode._pending[2]["question"], second)
        self.assertEqual(self.mode._pending[2]["options"], choices[::-1])
        self.mode.post("/api/native/stop", {"session": self.token})
        self.mode._handle_frame(self.session, grid, 1.6)
        self.assertIsNone(self.mode._pending)
        self.assertEqual(self.mode.get("/api/native/status", {"session": self.token})["state"], "ended")

    def test_manual_start_still_requires_regions(self):
        with self.assertRaises(ValueError):
            self.mode.post("/api/native/start", {"id": 42, "lectureIds": ["lecture"]})

    def test_footer_count_is_part_of_identity_and_live_override_invalidates_old_answer(self):
        question = "Какие форматы хранят данные?"
        options = ["JSON", "CSV", "MP3", "WAV"]
        grid = [line(question, .12, .30, .52, .04)]
        grid.extend(line(f"{i+1}. {value}", .12 + (i % 2)*.4, .43 + (i//2)*.11, .18)
                    for i, value in enumerate(options))

        def feed(instruction):
            for _ in range(2):
                self.mode._handle_frame(self.session, grid + [line(instruction, .12, .64, .38)])

        for count, word in [(2, "два"), (3, "три")]:
            feed(f"Выберите {word} варианта.")
            self.assertEqual(self.mode._pending[2]["selectionMode"], "multiple")
            self.assertEqual(self.mode._pending[2]["expectedCount"], count)
            self.assertEqual(self.mode._pending[2]["options"], options)
            self.mode._pending = None
        feed("Выберите пять вариантов.")
        self.assertEqual(self.session["selectionMode"], "unknown")
        self.assertIn("Уточни режим", self.session["message"])
        self.assertIsNone(self.mode._pending)
        self.mode.post("/api/native/mode", dict(session=self.token, selectionMode="multiple"))
        feed("Выберите пять вариантов.")
        self.assertEqual(self.mode._pending[2]["selectionMode"], "multiple")
        self.assertIsNone(self.mode._pending[2]["expectedCount"])
        self.session.update(state="answer", answer={"choices": [1, 2]})
        old_revision = self.session["revision"]
        self.mode.post("/api/native/mode", dict(session=self.token, selectionMode="single"))
        self.assertGreater(self.session["revision"], old_revision)
        self.assertIsNone(self.mode._pending)
        self.assertIsNone(self.session["answer"])
        feed("Выберите два варианта.")
        self.assertEqual(self.mode._pending[2]["selectionMode"], "single")
        self.assertEqual(self.mode._pending[2]["expectedCount"], 1)
        status = self.mode.get("/api/native/status", dict(session=self.token))
        self.assertEqual(status["requestedMode"], "single")
        self.assertEqual(status["expectedCount"], 1)

    def test_focus_discards_sidebar_and_surfaces_window_resize(self):
        self.mode.post("/api/native/stop", {"session": self.token})
        with patch.object(self.mode, "_executable", return_value="WindowReader"), \
                patch("native_mode.subprocess.Popen"), patch("native_mode.Thread.start"):
            self.mode.post("/api/native/start", dict(id=42, auto=True, focus=[.05,.10,.56,.83],
                          focusAspect=1.6, lectureIds=["lecture"], record=True))
        session = self.mode._session
        question = "Какое значение истинно?"
        frame = [line(question, .12, .28, .28)]
        values = ["Первое", "Второе", "Третье", "Четвёртое", "Пятое"]
        frame += [line(f"{i+1}. {v}", .12, .38+i*.07, .19) for i,v in enumerate(values)]
        frame += [line("Какой ещё вопрос?", .75, .25, .20),
                  line("1. Шум в чате", .75, .40, .19)]
        for _ in range(2):
            self.mode._handle_frame(session, frame, 1.6)
        pending = self.mode._pending[2]
        self.assertEqual(pending["options"], values)
        self.assertEqual(pending["channel"], "native")
        self.assertTrue(pending["record"])
        self.assertEqual(len(pending["captureTrace"]), 1)
        self.assertEqual(pending["captureTrace"][0]["question"], question)
        self.assertEqual(pending["captureTrace"][0]["options"], values)
        self.assertGreaterEqual(pending["captureMs"], 0)
        for _ in range(2):
            self.mode._handle_frame(session, frame, 1.0)
        self.assertIsNone(self.mode._pending)
        self.assertIn("Размер окна", session["message"])

    def test_twice_and_eight_manual_regions_remain_distinct(self):
        self.mode.post("/api/native/stop", {"session": self.token})
        for number in [2,8]:
            with self.subTest(number=number), patch.object(self.mode, "_executable", return_value="WindowReader"), \
                    patch("native_mode.subprocess.Popen"), patch("native_mode.Thread.start"):
                areas = [[.1, i/(number+1), .7, .08] for i in range(number+1)]
                result = self.mode.post("/api/native/start", dict(id=42, regions=areas, lectureIds=["lecture"]))
                session = self.mode._session
                values = [f"Пример {i+1}" for i in range(number)]
                fields = ["Какое значение подходит?", *values]
                lines = [dict(region=i, text=text) for i,text in enumerate(fields)]
                for _ in range(2):
                    self.mode._handle_frame(session, lines)
                payload = self.mode._pending[2]
                self.assertEqual(payload["options"], values)
                self.assertFalse(payload["record"])
                self.assertNotIn("captureTrace", payload)
                self.mode.post("/api/native/stop", {"session": result["session"]})
