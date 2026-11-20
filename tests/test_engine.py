import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import threading
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from engine import LectureIndex, import_document, list_models, solve
from semantic import _Hybrid


def tiny_pdf():
    stream = b"BT /F1 16 Tf 20 100 Td (Precision and Recall) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 140] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return pdf


class LectureTests(unittest.TestCase):
    def test_cell_id_and_markup(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "безопасное чтение.ipynb"
            path.write_text(json.dumps({"cells":[
                {"cell_type":"code", "source":["display(HTML('<b>Русский</b> текст<img src=\"data:image/png;base64,AAA\">'))"]},
                {"cell_type":"markdown", "source":["**Медиана** — центральный элемент."]},
                {"cell_type":"code", "source":["raise AssertionError()"],"outputs":[{"text":"не индексировать"}]},
            ]}), encoding="utf-8")
            rows = import_document(path)
            self.assertEqual([(r["location"],r["text"]) for r in rows[:2]],
                             [("ячейка 1","Русский текст"), ("ячейка 2","Медиана — центральный элемент.")])
            self.assertEqual(rows[2]["text"], "raise AssertionError()")

    def test_pptx_display_order(self):
        drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
        ppt = "http://schemas.openxmlformats.org/presentationml/2006/main"
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "лекция.pptx"
            with zipfile.ZipFile(path,"w") as archive:
                for name in ("slide1","slide2"):
                    archive.writestr("ppt/slides/"+name+".xml",f'<p:sld xmlns:p="{ppt}" xmlns:a="{drawing}"><a:p><a:r><a:t>{name}</a:t></a:r></a:p></p:sld>')
                archive.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{ppt}" xmlns:r="{rel}"><p:sldIdLst><p:sldId r:id="b"/><p:sldId r:id="a"/></p:sldIdLst></p:presentation>')
                archive.writestr("ppt/_rels/presentation.xml.rels",'<Relationships><Relationship Type="slide/slide" Id="a" Target="slides/slide1.xml"/><Relationship Type="slide/slide" Id="b" Target="slides/slide2.xml"/></Relationships>')
            self.assertEqual([(r["location"],r["text"]) for r in import_document(path)],
                             [("слайд 1","slide2"),("слайд 2","slide1")])

    @unittest.skipUnless(shutil.which("pdftotext") or shutil.which("swift"), "missing PDF reader")
    def test_pdf_page(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "example.pdf"
            path.write_bytes(tiny_pdf())
            result = import_document(path)
            self.assertEqual(result[0]["location"],"стр. 1")
            self.assertIn("Precision and Recall",result[0]["text"])

    def test_exact_card_reorder_vs_changed_question_and_options(self):
        question = "Как изменился рост на 15%?"
        original = ["5 %", "10 %", "15 %", "20 %"]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "пример.quiz.json"
            path.write_text(json.dumps({"cards":[{"question":question,"answer":"15 %","options":original}]}))
            index = LectureIndex(import_document(path))
        self.assertEqual(solve(question, ["20%","15%","10%","5%"], index)["choice"],2)
        for changed in (question.replace("15", "1,5"), question.replace("рост","не рост")):
            self.assertIsNone(solve(changed, original, index)["choice"])
        self.assertIsNone(solve(question, ["25%","15%","10%","5%"], index)["choice"])

    def test_fused_ordinal_is_not_a_new_answer_even_if_model_would_choose_another(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        cards = import_document(examples / "analytics-demo.quiz.json")
        lecture = import_document(examples / "analytics-demo.txt")
        card = next(c for c in cards if "Магазин посетили 200" in c.get("question", ""))
        index = LectureIndex([*cards, *lecture])
        with patch("engine._api") as complete:
            result = solve(card["question"], ["2%", "24%", "8%", "16%"], index, "test")
            self.assertIsNone(result["choice"])
            self.assertIn("распознанные варианты", result["detail"])
            self.assertEqual(solve(card["question"], ["8%", "16%", "2%", "4%"], index, "test")["choice"], 4)
            complete.assert_not_called()

        # Another authored card for the exact question and actual option set takes precedence.
        other = dict(card, options=["2%", "24%", "8%", "16%"], answer="24%")
        actual = solve(card["question"], other["options"], LectureIndex([card, other]))
        self.assertEqual(actual["choice"], 2)

    def test_percent_choice_must_agree_with_cited_conversion_for_the_same_counts(self):
        question = "Магазин посетили 200 человек. Покупку сделали 8 человек. Какова конверсия?"
        quote = "При 200 посетителях и 8 покупателях конверсия равна 8 / 200 × 100 % = 4 %."
        index = LectureIndex([{"source": "Лекция", "location": "1", "text": quote}])
        options = ["2%", "24%", "8%", "16%"]
        completion = {"choices": [{"message": {"content": '{"choice":3,"evidence":1}'}}]}
        with patch("engine._api", return_value=completion):
            result = solve(question, options, index, "test")
            self.assertEqual((result["choice"], result["quote"]), (None, quote))
            self.assertIn("расходится", result["detail"])
            self.assertEqual(solve(question, ["2%", "8%", "4,0%", "16%"], index, "test")["choice"], 3)
            for changed in (question.replace("200", "100"), question.replace("Какова конверсия", "Какая конверсия неверна"),
                            question.replace("Какова конверсия", "Какова точность")):
                self.assertEqual(solve(changed, options, index, "test")["choice"], 3)

    def test_quote_grounding(self):
        quote = "Recall учитывает пропущенные положительные случаи."
        index = LectureIndex([{"source":"lecture.txt","location":"стр. 1","text":quote}])
        options = ["Accuracy","Recall","Precision","F1"]
        for answer, expected in [
            ({"choice":2,"id":"S1","quote":quote},2),
            ({"choice":2,"id":"S2","quote":quote},None),
            ({"choice":2,"id":"S1","quote":"выдуманная цитата"},None),
            ({"choice":True,"id":"S1","quote":quote},None),
            ({"choice":0,"id":"","quote":""},None),
        ]:
            completion = {"choices":[{"message":{"content":json.dumps(answer,ensure_ascii=False)}}]}
            with patch("engine._api", return_value=completion):
                self.assertEqual(solve("Какая метрика учитывает пропуски?",options,index,"test")["choice"],expected)

    def test_bundled_diagnostics_find_the_reference_cell_for_each_question(self):
        project = Path(__file__).resolve().parents[1]
        dataset = json.loads((project / "ui/check-questions.json").read_text(encoding="utf-8"))
        lecture = project / "examples" / dataset["lecture"]
        digest = hashlib.sha256(lecture.name.encode() + b"\0" + lecture.read_bytes())
        self.assertEqual(dataset["lectureId"], digest.hexdigest())
        index = LectureIndex(import_document(lecture))
        for group in ("items", "challenge", "multiple"):
            for card in dataset[group]:
                with self.subTest(question=card["question"]):
                    best = index.search(card["question"], card["options"], limit=1)[0]
                    self.assertEqual(best["location"], f'ячейка {card["cell"]}')

    def test_misread_acronym_uses_documented_spelling_for_search_and_model(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        index = LectureIndex(import_document(examples / "QuizLens-demo.ipynb"))
        for token, expected, cell in [("АРІ", "API", 3), ("AРІ", "API", 3), ("СЅV", "CSV", 2)]:
            self.assertEqual(index.normalize_ocr_text(token), expected)
            question = "Что такое " + token + "?"
            self.assertEqual(index.search(question)[0]["location"], f"ячейка {cell}")
        options = ["Таблица", "Запрос к сервису", "Диаграмма", "Отчёт"]
        with patch("engine._api", return_value={}) as complete:
            solve("Что такое АРІ?", options, index, "test")
        prompt = json.loads(complete.call_args.args[2]["messages"][1]["content"])
        self.assertEqual(prompt["question"], "Что такое API?")
        self.assertIn("интерфейс для обмена запросами", prompt["fragments"][0]["text"])
        self.assertEqual(index.normalize_ocr_text("API или АРІ"), "API или АРІ")
        competing = ["АРІ", "API", "А", "A"]
        self.assertEqual(index.normalize_ocr_fields(["Прочти АРІ", *competing]), ["Прочти API", *competing])
        self.assertEqual(index.normalize_ocr_text("не 1,5 > −2; АЗІ; data_АРІ"), "не 1,5 > −2; АЗІ; data_АРІ")
        ambiguous = LectureIndex([{"source":"L", "location":"1", "text":"Коды API и АРІ различны."}])
        self.assertEqual(ambiguous.normalize_ocr_text("AРІ"), "AРІ")

    def test_cannot_cite_solved_card_with_different_number_polarity_or_options(self):
        options = ["10%", "30%", "70%", "90%"]
        card = {"source":"QA", "location":"1", "question":"Для каких 30 объектов утверждение неверно?",
                "options":options, "answer":"30%"}
        card["text"] = card["question"] + "\n" + card["answer"]
        index = LectureIndex([card])
        store = Mock()
        store._query_vector.return_value = [1.0]
        hybrid = _Hybrid(index, store, "test", [[1.0]])
        for reader in (index, hybrid):
            for question, choices in (
                (card["question"].replace("30", "80"), options),
                (card["question"].replace("неверно", "верно"), options),
                (card["question"].replace("30 ", ""), options),
                (card["question"], ["10%", "30%", "60%", "90%"]),
            ):
                with patch("engine._api") as complete:
                    self.assertIsNone(solve(question, choices, reader, "test")["choice"])
                complete.assert_not_called()
            self.assertEqual(solve(card["question"], options[::-1], reader)["choice"], 3)

    def test_two_letter_variables_must_match_a_whole_documented_formula(self):
        index = LectureIndex(import_document(Path(__file__).resolve().parents[1] / "examples/analytics-demo.quiz.json"))
        q = "Какое утверждение о Recall и Precision неверно?"
        card = index.sections[index.cards[q[:-1].casefold()][0]]
        formula = card["options"][-1]
        observed = formula.replace("TP", "ТР", 1)
        other = card["options"][:-1]
        self.assertEqual(solve(q, [observed, *other], index)["choice"], 4)
        self.assertEqual(solve(q, [*other, formula.replace("TP", "ТР")], index)["choice"], 3)
        changed = observed.replace("+", "-")
        self.assertEqual(index.normalize_ocr_fields([q, changed])[-1], changed)
        switched = observed.replace("FP", "FN")
        self.assertEqual(index.normalize_ocr_fields([q, switched])[-1], switched)
        self.assertEqual(index.normalize_ocr_fields([q, observed, formula]), [q, observed, formula])
        self.assertEqual(index.normalize_ocr_fields(["Какие буквы кириллические?", observed])[-1], observed)
        # If that spelling is intentionally part of the source material, leave it intact.
        conflicting = LectureIndex([card, {**card, "options": [*other, observed]}])
        self.assertEqual(conflicting.normalize_ocr_fields([q, observed])[-1], observed)

    def test_unambiguous_acronym_can_recover_exact_card_without_model(self):
        options = ["API", "Выплата", "Отчёт", "Формат"]
        question = "Какой из вариантов НЕ описывает API?"
        card = {"source": "card", "location": "1", "question": question, "options": options,
                "answer": "Выплата", "text": question + "\nВыплата"}
        index = LectureIndex([card])
        misread = question.replace("API", "АРІ")
        with patch("engine._api") as complete:
            result = solve(misread, ["Формат", "Отчёт", "Выплата", "АРІ"], index, "test")
            self.assertEqual((result["choice"], result["kind"]), (3, "exact"))
            complete.assert_not_called()
        self.assertIsNone(solve(misread.replace(" НЕ ", " "), options, index)["choice"])
        self.assertIsNone(solve(misread, ["API", "АРІ", "Выплата", "Формат"], index)["choice"])

    def test_table_evidence_is_selected_by_id_and_displayed_verbatim(self):
        row = "| CSV | Разделители, кодировка, даты и пропуски |"
        table = "## Форматы\n\n| Формат | Риски |\n|:----|-----:|\n| XLSX | Объединённые ячейки |\n" + row
        index = LectureIndex([{"source": "Форматы", "location": "слайд 3", "text": table}])
        options = ["ZIP", "CSV", "XLSX", "DOCX"]

        def respond(_base_url, _endpoint, payload, timeout):
            fragments = json.loads(payload["messages"][1]["content"])["fragments"]
            self.assertEqual([p["text"] for p in fragments], ["| XLSX | Объединённые ячейки |", row])
            self.assertIn("Формат | Риски", fragments[1]["context"])
            self.assertEqual(len({p["id"] for p in fragments}), len(fragments))
            return {"choices": [{"message": {"content": json.dumps({"choice": 2, "evidence": str(fragments[1]["id"])})}}]}

        with patch("engine._api", side_effect=respond):
            result = solve("У какого формата с разделителями возможны проблемы?", options, index, "test")
        self.assertEqual((result["choice"], result["kind"], result["source"], result["quote"]),
                         (2, "model", "Форматы · слайд 3", row))

    def test_unknown_evidence_never_falls_back_to_a_choice(self):
        text = "Медиана сохраняет устойчивость к единичным выбросам."
        index = LectureIndex([{"source":"Лекция","location":"стр. 2","text":text}])
        options = ["Среднее", "Медиана", "Сумма", "Максимум"]
        for selection in (
            {"choice": 2, "evidence": 8, "id": "S1", "quote": text},
            {"choice": 2, "evidence": True},
            {"choice": 2},
            {"choice": 0, "evidence": 0},
        ):
            completion = {"choices": [{"message": {"content": json.dumps(selection)}}]}
            with patch("engine._api", return_value=completion):
                result = solve("Что устойчиво к выбросам?", options, index, "test")
            self.assertIsNone(result["choice"])
            self.assertEqual((result["kind"], result["quote"]), ("retrieval", text))

    def test_local_endpoint_and_no_redirect(self):
        qwen = "qwen/qwen3-4b-instruct-2507"
        catalog = [
            {"id": "phonon-40m-multilingual"},
            {"id": "gradium/phonon-40m-multilingual-GGUF"},
            {"id": "my-speaker", "type": "tts"},
            {"id": qwen},
            {"id": "embedded-systems-tutor"},
            {"id": "my-custom-name"},
            {"id": "nomic-ai/nomic-embed-text-v1.5"},
            {"id": "my-search-model", "type": "embeddings"},
        ]

        class LocalModel(BaseHTTPRequestHandler):
            def do_GET(self):
                self.assert_path()
                if self.server.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://example.invalid/v1/models")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": catalog}).encode())

            def assert_path(self):
                if self.path != "/v1/models":
                    raise AssertionError(self.path)

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.last_chat_request = prompt
                material = json.loads(prompt["messages"][1]["content"])
                hit = material["fragments"][0]
                chosen = next(key for key, option in material["options"].items() if option == "Recall")
                answer = {"choice": int(chosen), "evidence": hit["id"]}
                self.send_response(200)
                self.end_headers()
                result = {"choices":[{"message":{"content":json.dumps(answer, ensure_ascii=False)}}]}
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1",0), LocalModel) as model_server:
            model_server.redirect = False
            thread = threading.Thread(target=model_server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{model_server.server_port}/v1"
                models = list_models(base_url)
                self.assertEqual(models,[qwen, "embedded-systems-tutor", "my-custom-name"])
                index = LectureIndex([{"source":"Лекция","location":"стр. 3","text":"Recall учитывает пропуски."}])
                result = solve("Какая метрика учитывает пропуски?", ["A","B","C","Recall"],
                               index, models[0], base_url)
                request = model_server.last_chat_request
                self.assertEqual(request["model"], qwen)
                self.assertFalse(request["stream"])
                self.assertEqual([m["role"] for m in request["messages"]], ["system", "user"])
                self.assertEqual((result["choice"],result["source"],result["quote"]),
                                 (4,"Лекция · стр. 3","Recall учитывает пропуски."))
                model_server.redirect = True
                with self.assertRaisesRegex(RuntimeError,"302"):
                    list_models(base_url)
            finally:
                model_server.shutdown()
                thread.join()
        with self.assertRaises(ValueError):
            list_models("http://example.com:1234/v1")
