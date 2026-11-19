import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
import training


class Training(unittest.TestCase):
    def test_curated_cards_have_changes_correct_keys_and_real_sources(self):
        deck = training.builtin()
        lecture = (ROOT / "examples" / deck["lectureName"]).read_text(encoding="utf-8")
        self.assertEqual(len(deck["cards"]), 10)
        for card in deck["cards"]:
            self.assertIn(card["excerpt"], lecture)
            self.assertTrue(card["source"].startswith(deck["lectureName"]))
            variants = card["variants"]
            self.assertEqual(len({v["question"] for v in variants}), len(variants))
            self.assertEqual(len({tuple(v["correct"]) for v in variants}), len(variants))
            for v in variants:
                self.assertIn(v["value"], v["question"])
                self.assertTrue(all(1 <= i <= len(card["options"]) for i in v["correct"]))
                self.assertTrue(v["reason"])
        p = deck["cards"][0]
        for v in p["variants"]:
            rate = 48 / int(v["value"]) * 100
            self.assertEqual(float(p["options"][v["correct"][0]-1].split()[0]), rate)
        for v in deck["cards"][4]["variants"]:
            self.assertEqual(len(v["correct"]), 2)

    def setUp(self):
        self.source = "В лаборатории 8 наблюдений. Точность положительных прогнозов определяется долей верных предупреждений."
        self.index = engine.LectureIndex([{"source":"Своя лекция.md","location":"§ 2","text":self.source}])
        self.valid = {"fragment":1, "template":"Сколько получится, если из 8 вычесть [X]?",
                      "options":["5","6","7"],
                      "conditions":[{"value":"1","correct":3,"reason":"8 − 1 = 7."},
                                    {"value":"2","correct":2,"reason":"8 − 2 = 6."}]}

    def test_generated_questions_keep_real_provenance_and_discard_invalid_items(self):
        output = {"choices":[{"message":{"content":json.dumps({"cards":[{"junk":0}, self.valid, self.valid]})}}]}
        with patch.object(engine, "_api", return_value=output) as call:
            deck = training.generate(self.index, "Qwen")
        self.assertTrue(deck["generated"])
        self.assertEqual(len(deck["cards"]), 1)
        card = deck["cards"][0]
        self.assertEqual(card["excerpt"], self.source)
        self.assertEqual(card["source"], "Своя лекция.md · § 2")
        self.assertEqual(card["variants"][1]["question"],"Сколько получится, если из 8 вычесть 2?")
        self.assertEqual(card["variants"][1]["correct"], [2])
        sent = json.loads(call.call_args.args[2]["messages"][1]["content"])
        self.assertEqual(sent[0]["text"], self.source)

    def test_rejects_bogus_keys_unsupported_sources_and_unchanged_pairs(self):
        for label, transform in [
            ("fabricated source", lambda c: c.update(fragment=7)),
            ("missing placeholder", lambda c: c.update(template="Самостоятельный вопрос?")),
            ("duplicate", lambda c: c.update(options=["ABC","abc"])),
            ("boolean key", lambda c: c["conditions"][0].update(correct=True)),
            ("wrong index", lambda c: c["conditions"][0].update(correct=8)),
            ("no new answer", lambda c: c["conditions"][0].update(correct=2)),
            ("no new condition", lambda c: c["conditions"][0].update(value="2"))
        ]:
            with self.subTest(label):
                faulty = copy.deepcopy(self.valid)
                transform(faulty)
                self.assertIsNone(training._draft_card(faulty,[{"source":"a","text":"b"}]))
        with self.assertRaises(ValueError):
            training.generate(self.index, "")
        with self.assertRaises(ValueError):
            training.generate(engine.LectureIndex([]), "Qwen")
        with patch.object(engine, "_api", return_value={"choices":[]}):
            with self.assertRaises(ValueError): training.generate(self.index, "Qwen")


if __name__ == "__main__":
    unittest.main()
