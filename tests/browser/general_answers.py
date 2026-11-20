"""Exercise automatic local-model answers and the opt-out with the real server."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from playwright.sync_api import expect

from manual_window import OPTIONS, QUESTION, uncheck_sources


MODEL = "test/local-tutor"
PROVENANCE = "Ответ модели · без источника"


@contextmanager
def local_model(choice=1):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/v1/models":
                self.send_json({"data": [{"id": MODEL}]})
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_json({"choices": [{"message": {"content": json.dumps({"choices": [choice]})}}],
                            "usage": {"prompt_tokens": 123, "completion_tokens": 8}})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            thread.join()


def check_general_answers(browser, url, calls, directory):
    context = browser.new_context(viewport={"width": 1280, "height": 810}, locale="ru-RU", reduced_motion="reduce")
    page = context.new_page()
    requests = []

    def record_solve(request):
        if request.url.endswith("/api/solve"):
            requests.append(request.post_data_json)

    def read_question():
        # Exercise the usual capture-to-answer controller. Only supply the recognized frame.
        page.evaluate("""fields => {
            window.quizLensTabCapture.start();
            window.quizLensTabCapture.read(fields, {selectionMode:'single', expectedCount:1});
        }""", [QUESTION, *OPTIONS])

    page.on("request", record_solve)
    try:
        page.goto(url)
        uncheck_sources(page)
        model = page.locator("#model")
        toggle = page.locator("#withoutDocuments")
        expect(toggle).to_be_disabled()
        model.select_option(MODEL)
        expect(toggle).to_be_enabled()
        expect(toggle).to_be_checked()

        answer = page.locator("#answer")
        title = answer.locator(".answer-title")
        action = answer.get_by_role("button", name="Ответить без документов", exact=True)
        assert not requests and not calls

        with page.expect_response(lambda response: response.url.endswith("/api/solve"), timeout=15000) as observed:
            read_question()
        response = observed.value
        assert response.ok, response.text()
        expect(toggle).to_be_checked()
        result = response.json()
        assert result["kind"] == "model_knowledge", result
        assert not any(result.get(key) for key in ("source", "excerpt", "quote", "evidence")), result
        assert len(requests) == 1 and requests[0]["withoutDocuments"] is True
        assert requests[0]["lectureIds"] == [] and requests[0]["model"] == MODEL
        assert requests[0]["record"] is False
        assert len(calls) == 1 and calls[0]["model"] == MODEL
        system, user = calls[0]["messages"]
        assert "по своим знаниям" in system["content"]
        prompt = json.loads(user["content"])
        assert prompt["question"] == QUESTION and [item["text"] for item in prompt["options"]] == list(OPTIONS)
        assert not set(prompt).intersection({"fragments", "source", "evidence"})
        expect(title).to_have_text(OPTIONS[0])
        expect(answer.get_by_text(PROVENANCE, exact=True)).to_be_visible()
        expect(answer.locator(".answer-source, .answer-excerpt")).to_have_count(0)
        if directory:
            page.locator("#windowStep").evaluate("element => element.scrollIntoView({block:'start'})")
            page.screenshot(path=directory / "general-model.png", animations="disabled")

        toggle.uncheck()
        expect(title).to_have_text("Текст распознан")
        expect(answer.get_by_text(PROVENANCE, exact=True)).to_have_count(0)
        expect(page.locator("#readText li")).to_have_text(list(OPTIONS))

        action.click()
        expect(toggle).to_be_checked()
        expect(title).to_have_text(OPTIONS[0])
        expect(answer.get_by_text(PROVENANCE, exact=True)).to_be_visible()

        toggle.uncheck()
        page.reload()
        expect(model).to_have_value(MODEL)
        expect(toggle).to_be_enabled()
        expect(toggle).not_to_be_checked()
        expect(page.locator("#lectureList input:checked")).to_have_count(0)
        read_question()
        expect(title).to_have_text("Текст распознан")
        expect(action).to_be_visible()
        assert len(requests) == 1 and len(calls) == 1
        print("Verified automatic model answers, no source claims or journal input, and opt-out after refresh.")
    finally:
        context.close()
