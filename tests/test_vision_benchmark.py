import unittest

try:
    from vision_benchmark import Case, score, summarize
except ModuleNotFoundError as exc:
    if exc.name != "PIL":
        raise
    Case = None


@unittest.skipUnless(Case, "Pillow is required for the rendered benchmark")
class BenchmarkScoring(unittest.TestCase):
    def test_sign_decimal_negation_and_reordered_answers_are_observable(self):
        case = Case("example", "Что НЕ изменилось?", ("−1,5 %", "5 %"))
        row = score(case, {"texts": ["Что изменилось?", "15 %", "5%"], "readingWarnings": [{"field": 1}]})
        self.assertTrue(row["complete"])
        self.assertFalse(row["criticalExact"])
        self.assertEqual(row["unflaggedCriticalErrors"], [0])
        self.assertFalse(score(case, {"texts": [case.question, "5 %", "-1.5 %"]})["optionsInOrder"])
        ok = score(case, {"texts": [case.question, "−1.5%", "5%"]})
        self.assertTrue(ok["criticalExact"] and ok["optionsInOrder"])
        partial = score(case, {"texts": []})
        summary = summarize([{**ok, "ms": 10}, {**partial, "ms": 15}])
        self.assertEqual(summary["layoutComplete"], 1)
        self.assertEqual(summary["sameReadingAfterTimerChange"], 0)
        self.assertEqual(summary["framesWithUnflaggedCriticalErrors"], 0)
