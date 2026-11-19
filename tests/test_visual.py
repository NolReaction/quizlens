import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual

# One-pixel PNG. Real image bytes still travel through the JSON image_url endpoint.
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+iAisAAAAASUVORK5CYII="


class ModelServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, value, status=200):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/v1/models":
            self.send_json({"models": [
                {"type": "llm", "key": "local/figures", "capabilities": {"vision": True},
                 "loaded_instances": [{"id": "plot-alias"}]},
                {"type": "llm", "key": "pretend-vl", "capabilities": {"vision": False}},
            ]})
        elif self.path == "/v1/models":
            self.send_json({"data": [{"id": x} for x in ("plot-alias", "qwen3-4b-instruct-2507", "pretend-vl", "qwen2-vl", "text-embedding")]})
        else:
            self.send_json({}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.send_json({}, 404)
        self.server.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_json({"choices": [{"message": {"content": json.dumps(self.server.answer)}}]})


class VisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelServer)
        cls.base = "http://127.0.0.1:%s/v1" % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.data = {"model": "plot-alias", "image": IMAGE, "question": "Какой максимум?",
                     "options": ["Январь", "Март", "Май"], "selectionMode": "single"}
        self.server.answer = {"question": "Какой максимум?", "options": self.data["options"],
                              "choices": [2], "observation": "Март — 23 заказа."}
        self.server.calls = []

    def solve(self, **kwargs):
        return visual.solve({**self.data, **kwargs}, base_url=self.base)

    def test_models_use_capabilities_first_and_keep_alias(self):
        self.assertEqual(visual.vision_models(self.base), ["local/figures", "plot-alias", "qwen2-vl"])
        for model in ("qwen3-4b-instruct-2507", "pretend-vl"):
            with self.assertRaises(ValueError):
                self.solve(model=model, visionConfirmed=True)
        with self.assertRaises(ValueError):
            self.solve(model="unclassified")
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.solve(model="unclassified", visionConfirmed=True)["choices"], [2])

    def test_local_payload_contains_question_image_and_no_reference(self):
        self.assertEqual(self.solve()["choices"], [2])
        content = self.server.calls[-1]["messages"][-1]["content"]
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": IMAGE}})
        self.assertEqual(set(json.loads(content[0]["text"])), {"source", "question", "options", "selectionMode"})

    def test_model_permutation_and_unknown_option(self):
        self.server.answer.update(options=["Май", "Январь", "Март"], choices=[1])
        self.assertEqual(self.solve()["choices"], [3])
        self.server.answer["options"][1] = "Август"
        self.assertEqual(self.solve()["choices"], [])

    def test_auto_reads_both_image_and_external_caption(self):
        self.server.answer["instruction"] = "Выберите два ответа"
        self.server.answer["choices"] = [1, 3]
        answer = self.solve(question="", options=[], selectionMode="auto")
        self.assertEqual(answer["choices"], [1, 3])
        self.assertEqual(answer["options"], self.data["options"])
        self.server.answer.update(question="Выберите один ответ", instruction="")
        self.assertEqual(self.solve(question="", options=[], selectionMode="auto")["choices"], [])

    def test_invalid_outputs_abstain(self):
        for choices in ([], [3, 3], [True], [4], [0], ["3"], [1, 2]):
            self.server.answer["choices"] = choices
            self.assertEqual(self.solve()["choices"], [], choices)
        self.server.answer.update(choices=[2], observation="")
        self.assertEqual(self.solve()["choices"], [])

    def test_missing_or_wrong_input_rejected_before_completion(self):
        for change in ({"image": "https://site/image"}, {"options": ["A"]}, {"options": [""] * 3},
                       {"options": ["1"] * 9}, {"model": ""}, {"selectionMode": "5"}):
            with self.assertRaises(ValueError):
                self.solve(**change)
        self.assertEqual(self.server.calls, [])

    def test_curated_figures_and_numeric_keys(self):
        cases = visual.exercises()
        right = [c["options"][c["choices"][0]-1] for c in cases]
        self.assertEqual(right, ["Май", "Март", "200 %", "Апрель", "9", "Реклама", "Рассылка", "80 %", "90 %", "11 кг"])
        self.assertEqual(len({c["id"] for c in cases}), 10)
        for c in cases:
            self.assertIn(c["figure"]["kind"], ("bar", "line", "table"))
            self.assertTrue(c["explanation"])
