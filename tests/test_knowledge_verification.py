import json
import unittest
from unittest.mock import patch

from engine import solve_with_model_knowledge


class KnowledgeVerificationTests(unittest.TestCase):
    def test_matches_element_symbols_with_their_literal_case_and_order(self):
        examples = (
            ("Какой химический символ у гелия?", ["HI", "He", "H", "Hi"], 2),
            ("Как обозначается химический элемент гелий?", ["BE", "He", "H", "Be"], 2),
            ("Каков химический символ меди?", ["C", "Co", "Cu", "Cp"], 3),
            ("Каков символ химического элемента свинца?", ["Pt", "Pb", "Sn"], 2),
            ("What is the chemical symbol for oganesson?", ["Og", "Ne", "O"], 1),
        )
        with patch("engine._api") as model:
            for question, options, choice in examples:
                with self.subTest(question=question):
                    result = solve_with_model_knowledge(question, options, "local-model")
                    self.assertEqual(result["choices"], [choice])
                    self.assertEqual(result["verification"], {"kind": "element_symbol", "symbol": options[choice - 1]})
                    self.assertEqual(result["tokenUsage"], {"source": "none"})
            model.assert_not_called()

    def test_does_not_infer_an_unseen_or_ambiguous_element_option(self):
        for options in (["Hl", "HE", "H", "Hi"], ["Hl", "He", "HE", "Hi"], ["Hl", "Нe", "H", "Hi"]):
            with self.subTest(options=options), patch("engine._api") as model:
                result = solve_with_model_knowledge("Какой химический символ у гелия?", options, "local-model")
                self.assertEqual(result["choices"], [])
                self.assertIn("регистр", result["detail"])
                model.assert_not_called()

    def test_computes_an_exact_percentage_without_model_or_materials(self):
        question = "Из 8 прогнозов 3 оказались верными. Рассчитайте Accuracy."
        with patch("engine._api") as model:
            result = solve_with_model_knowledge(question, ["75%", "62,5%", "37,5%", "0%"], "local-model")
            self.assertEqual(result["choices"], [3])
            self.assertEqual(result["verification"], {"kind": "calculation", "equation": "3 / 8 × 100 = 37,5%"})
            for options in (["75%", "50%"], ["37,5%", "37.50%", "0%"]):
                ambiguous = solve_with_model_knowledge(question, options, "local-model")
                self.assertEqual(ambiguous["choices"], [])
                self.assertIn("Расчёт", ambiguous["detail"])
            model.assert_not_called()

    def test_rejects_choice_number_and_text_disagreement(self):
        options = ["3 mg", "2 mg", "5 mg", "1 mg"]
        for text in ("1 mg", "5 Mg"):
            content = json.dumps({"choices": [{"id": 3, "text": text}]})
            response = {"choices": [{"message": {"content": content}}]}
            with self.subTest(text=text), patch("engine._api", return_value=response) as model:
                answer = solve_with_model_knowledge("Какую массу показали весы?", options, "local-model")
                self.assertEqual(answer["choices"], [])
                self.assertIn("расходятся", answer["detail"])
                model.assert_called_once()
