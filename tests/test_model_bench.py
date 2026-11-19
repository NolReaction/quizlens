import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_bench as bench


class AdvisorBenchmark(unittest.TestCase):
    def test_russian_questions_use_actual_retrieval_without_keys_in_the_prompt(self):
        calls = []
        answers = iter((((3, "Медиана делит"),), ((2, "Precision = TP / (TP + FP)"),),
                        ((4, "рост числа пропусков уменьшает"), (1, "Эти метрики могут различаться"))))

        def provider(base_url, path, payload=None, timeout=10):
            calls.append(path)
            if path == "models":
                return {"data": [{"id": "qwen/local"}]}
            self.assertEqual(path, "chat/completions")
            self.assertEqual(payload["model"], "qwen/local")
            prompt = json.loads(payload["messages"][1]["content"])
            self.assertEqual(set(prompt), {"question", "options", "fragments"})
            self.assertEqual(len(prompt["options"]), 4)
            self.assertGreaterEqual(len(prompt["fragments"]), 2)
            for row in prompt["fragments"]:
                self.assertTrue(row["text"])
                self.assertFalse({"correct", "choices", "answer", "expected"}.intersection(row))
            evidence = [{"choice": choice, "evidence": next(row["id"] for row in prompt["fragments"] if phrase in row["text"])}
                        for choice, phrase in next(answers)]
            reply = evidence[0] if len(evidence) == 1 else {"selections": evidence}
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}

        with patch.object(bench.engine, "_api", side_effect=provider):
            rows = [bench.run_case("qwen/local", i) for i in range(bench.CASE_COUNT)]
        self.assertEqual(calls, ["models", "chat/completions"] * 3)
        self.assertEqual([r["passed"] for r in rows], [True, True, True])
        self.assertEqual([r["firstCall"] for r in rows], [True, False, False])
        self.assertEqual([r["choices"] for r in rows], [[3], [2], [1, 4]])
        self.assertTrue(all(r["withinBudget"] and r["status"] == "ok" for r in rows))

    def test_late_vs_wrong_and_budget_boundary(self):
        cases = [
            (15000000000, [3], "model", "ok", True, True),
            (15000000001, [3], "model", "late", False, False),
            (22000000, [2], "model", "wrong", False, True),
            (22000000, [], "retrieval", "unconfirmed", False, True),
        ]
        with patch.object(bench.engine, "list_models", return_value=["local"]):
            for latency, choices, kind, status, passed, in_budget in cases:
                with self.subTest(status=status):
                    result = {"choices": choices, "kind": kind}
                    with patch.object(bench, "monotonic_ns", side_effect=[700, 700 + latency]), \
                            patch.object(bench.engine, "solve", return_value=result):
                        row = bench.run_case("local", 0)
                    self.assertEqual((row["status"], row["passed"], row["withinBudget"]), (status, passed, in_budget))
                    self.assertEqual(row["elapsedMs"], (latency + 999999) // 1000000)
                    self.assertEqual(row["withinLiveTimeout"], latency <= 10000000000)

    def test_invalid_unlisted_and_server_errors_do_not_start_a_model(self):
        with patch.object(bench.engine, "_api") as api:
            for model, idx in [("", 0), (None, 0), ("x\nfoo", 0), ("local", False), ("local", 3), ("local", -1)]:
                with self.subTest(model=model, idx=idx), self.assertRaises(ValueError):
                    bench.run_case(model, idx)
            api.assert_not_called()
            api.return_value = {"data": [{"id": "text-embedding-nomic-v1"}, {"type": "tts", "id": "speech"}]}
            row = bench.run_case("text-embedding-nomic-v1", 0)
            self.assertEqual((row["status"], row["elapsedMs"]), ("error", None))
            self.assertEqual(api.call_count, 1)
            api.side_effect = RuntimeError("Нет связи")
            self.assertIn("Нет связи", bench.run_case("local", 0)["detail"])
        with patch.object(bench.engine, "list_models", return_value=["local"]), \
                patch.object(bench.engine, "solve", side_effect=RuntimeError("Время ответа модели истекло.")):
            row = bench.run_case("local", 0)
            self.assertEqual(row["status"], "error")
            self.assertFalse(row["passed"])
            self.assertIn("истекло", row["detail"])
            self.assertIsNotNone(row["elapsedMs"])


if __name__ == "__main__":
    unittest.main()
