"""Local QuizLens server for desktop capture and practice."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection, HTTPException
from socketserver import TCPServer
from urllib.parse import parse_qs, urlsplit

import advisor
import components
from console_log import event
import extension_package
import engine
import model_bench
import ocr
import quiz_mode
import semantic
import training
import updater
import visual
from journal import Journal
from native_mode import NativeMode

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "packaging" / "VERSION").read_text(encoding="ascii").strip()
DEFAULT_PORT = 8765
MAX_IMPORT = 32 * 1024 * 1024
FORMATS = {".txt", ".md", ".pdf", ".ipynb", ".pptx"}
WEB_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".ico": "image/x-icon",
             ".png": "image/png", ".svg": "image/svg+xml"}
ENDPOINT = os.environ.get("QUIZ_LENS_LM_URL", "http://127.0.0.1:1234/v1")
# Browser selections store the original bundled bank's content hash.
BUNDLED_BANK_ID = "ce46f62e43985a2d6bf6d99b25b9856e8bf6e40d7e4e49f2b5e374fff7a4c934"


def user_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/QuizLens"
    if sys.platform == "win32":
        # Preserve the folder used by people who ran earlier releases from source.
        previous = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "QuizLens"
        if (previous / "lectures").is_dir():
            return previous
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "QuizLens"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "QuizLens"


def recognized(name):
    return Path(name).suffix.lower() in FORMATS or name.lower().endswith(".quiz.json")


@lru_cache(maxsize=1)
def release_links():
    # Release builds stamp the origin; a local checkout can run without Git or a remote.
    try:
        repository = (ROOT / "packaging" / "REPOSITORY").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    segments = repository.split("/")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository)
            or segments[-1] in (".", "..")):
        return None
    base = "https://github.com/" + repository
    return {"releases": base + "/releases/latest", "guide": base + "/blob/HEAD/docs/GUIDE.md"}


@lru_cache(maxsize=1)
def host_hardware():
    return advisor.detect_hardware()


def model_advice(manual_profile=None):
    if manual_profile is not None:
        result = advisor.recommend(manual_profile, model_ids=())
        return {**result, "models": [], "serverAvailable": None, "serverError": "", "caseCount": model_bench.CASE_COUNT,
                "quizTokenExample": model_bench.quiz_token_example()}
    profile = host_hardware()
    try:
        models = engine.list_models(base_url=ENDPOINT, timeout=3)
        error = ""
    except RuntimeError as exc:
        models, error = [], str(exc)
    return {**advisor.recommend(profile, model_ids=models), "models": models,
            "serverAvailable": not error, "serverError": error, "caseCount": model_bench.CASE_COUNT,
            "quizTokenExample": model_bench.quiz_token_example()}


class Corpus:
    def __init__(self, directory):
        self.folder = directory
        self.folder.mkdir(parents=True, exist_ok=True)
        self.entries = {}
        self.example_ids = set()
        self.cache = {}
        self.lock = threading.RLock()
        for path in sorted(self.folder.glob("*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["id"] == path.stem and isinstance(row["sections"], list):
                    self.entries[row["id"]] = row
            except (OSError, ValueError, KeyError, TypeError) as exc:
                event("Документы", "Не удалось прочитать сохранённый источник. Его можно добавить заново.",
                      level="ВНИМАНИЕ", error=exc, repeat_after=60)
        for path in sorted((ROOT / "examples").glob("*")):
            if path.is_file() and recognized(path.name):
                entry = self.add(path, bundled=path.name == "analytics-demo.quiz.json")
                self.example_ids.add(entry["id"])

    def description(self, row):
        return {"id": row["id"], "name": row["name"], "count": len(row["sections"]),
                "builtIn": row["id"] in self.example_ids}

    def list(self):
        with self.lock:
            return [self.description(row) for row in self.entries.values()]

    def preview(self, key, page):
        with self.lock:
            row = self.entries.get(key)
            if row is None:
                raise ValueError("Документ уже удалён. Обнови список источников.")
            page_size = 5
            pages = max(1, (len(row["sections"]) + page_size - 1) // page_size)
            if not 1 <= page <= pages:
                raise ValueError("Такой страницы в документе нет.")
            start = (page - 1) * page_size
            sections = [{"location": s["location"], "text": s["text"]}
                        for s in row["sections"][start:start + page_size]]
            return {**self.description(row), "page": page, "pages": pages, "sections": sections}

    def remove(self, key, forget_passages):
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Выбери документ из списка источников.")
        with self.lock:
            if key in self.example_ids:
                raise ValueError("Это встроенный пример. Сними отметку, чтобы исключить его из поиска.")
            row = self.entries.get(key)
            if row is None:
                return False
            removed = {s["text"] for s in row["sections"]}
            for other_key, other in self.entries.items():
                if other_key != key:
                    removed.difference_update(s["text"] for s in other["sections"])
            forget_passages(removed)
            (self.folder / (key + ".json")).unlink(missing_ok=True)
            del self.entries[key]
            self.cache.clear()
            return True

    def add(self, path, bundled=False):
        digest = hashlib.sha256(path.name.encode() + b"\0" + path.read_bytes()).hexdigest()
        key = BUNDLED_BANK_ID if bundled else digest
        with self.lock:
            existing = self.entries.get(key)
            if existing and (not bundled or (existing.get("bundleRevision") == digest and
                                             existing.get("cardIndexVersion") == 2)):
                return self.description(existing)
            # A manual import of the current built-in file reuses its selection.
            seed = self.entries.get(BUNDLED_BANK_ID, {})
            if not bundled and seed.get("bundleRevision") == digest:
                return self.description(seed)
        sections = engine.import_document(path)
        if not sections:
            raise ValueError("В файле не найден читаемый текст.")
        row = {"id": key, "name": path.name, "sections": sections}
        if bundled:
            row["bundleRevision"] = digest
            row["cardIndexVersion"] = 2
        with self.lock:
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.folder, prefix=".import-", mode="w",
                                                 encoding="utf-8", delete=False) as handle:
                    tmp = Path(handle.name)
                    json.dump(row, handle, ensure_ascii=False)
                tmp.replace(self.folder / (key + ".json"))
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            self.entries[key] = row
            self.cache.clear()
        return self.description(row)

    def index(self, ids):
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("Отметь хотя бы один документ.")
        key = tuple(sorted(set(ids)))
        with self.lock:
            if any(i not in self.entries for i in key):
                raise ValueError("Обнови список документов и выбери источники снова.")
            if key not in self.cache:
                if len(self.cache) >= 8:
                    self.cache.clear()
                sections = [s for i in key for s in self.entries[i]["sections"]]
                self.cache[key] = engine.LectureIndex(sections)
            return self.cache[key]


class QuizServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address):
        # http.server can also encounter a closed peer before dispatch or during final flush.
        error = sys.exc_info()[1]
        if isinstance(error, OSError) and client_disconnected(error):
            return
        event("Сервер", "Ошибка обработки подключения. Для диагностики запусти с QUIZLENS_DEBUG=1.",
              level="ОШИБКА", error=error, repeat_after=60)

    def server_bind(self):
        # HTTPServer resolves its advertised name via reverse DNS. This loopback-only app
        # already knows its address, so offline DNS must not delay a launch or update.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def solve(self, data):
        started = time.monotonic()
        q, options, model = data.get("question"), data.get("options"), data.get("model", "")
        if not isinstance(q, str) or not 0 < len(q.strip()) <= 6000:
            raise ValueError("Не удалось прочитать вопрос.")
        if (not isinstance(options, list) or not engine.MIN_OPTIONS <= len(options) <= engine.MAX_OPTIONS or
                not all(isinstance(o, str) and 0 < len(o.strip()) <= 3000 for o in options)):
            raise ValueError("Нужны от 2 до 8 читаемых вариантов ответа.")
        if not isinstance(model, str) or len(model) > 500:
            raise ValueError("Проверь выбранную модель.")
        mode = data.get("selectionMode", "auto")
        if mode not in ("auto", "single", "multiple"):
            raise ValueError("Выбери режим ответа.")
        count = data.get("expectedCount")
        if count is not None and (type(count) is not int or not 1 <= count <= len(options)):
            raise ValueError("Число правильных ответов должно укладываться в число видимых вариантов.")
        hint = quiz_mode.effective_hint(quiz_mode.selection_hint(q, options), mode)
        if count is not None and hint["expectedCount"] not in (None, count):
            hint = {"selectionMode": "unknown", "expectedCount": None}
        if hint["selectionMode"] == "unknown":
            return {**hint, "choices": [], "choice": None, "evidence": [], "kind": "none", "source": "", "excerpt": "",
                    "tokenUsage": {"source": "none"},
                    "detail": "Указания о числе ответов расходятся. Проверь формулировку и выбери режим ответа."}
        if count is not None:
            hint["expectedCount"] = count
        question = q.strip()
        options = quiz_mode.clean_options([o.strip() for o in options])
        ids = data.get("lectureIds")
        index = self.corpus.index(ids)
        channel = data.get("channel", "")
        record = data.get("record") is True and channel in ("browser", "native")
        capture_ms = data.get("captureMs")
        if type(capture_ms) not in (int, float) or not 0 <= capture_ms <= 600000:
            capture_ms = None
        mode, count = hint["selectionMode"], hint["expectedCount"]
        result = None
        journal_warning = ""
        if channel not in ("training", "check"):
            try:
                result = self.journal.lookup(question, options, ids, mode, count)
            except (sqlite3.Error, OSError):
                journal_warning = "Журнал временно недоступен."
        if result is None:
            try:
                index = self.meaning.wrap(index, self.embedding_model)
                # Training distinguishes late correct answers from timeouts.
                request_timeout = 28 if channel == "training" else 10
                result = engine.solve(question, options, index, model, base_url=ENDPOINT, timeout=request_timeout,
                                      selection_mode=mode, expected_count=count)
            except RuntimeError as error:
                if not record:
                    raise
                result = {"choices": [], "choice": None, "evidence": [], "kind": "error", "detail": str(error),
                          "tokenUsage": {"source": "unknown"}}
        choices = result["choices"]
        evidence = [{"choice": item["choice"], "source": item["source"], "excerpt": item["quote"]}
                    for item in result["evidence"]]
        response = {**hint, "choice": result["choice"], "choices": choices, "evidence": evidence, "kind": result["kind"],
                "source": result.get("source", ""), "excerpt": result.get("quote", ""),
                "tokenUsage": result.get("tokenUsage", {"source": "none"}),
                "detail": result.get("detail") or ("" if choices else
                ("Подтверждённого ответа в карточках нет. Для разбора лекции выбери модель."
                 if not model else "В найденном тексте модель не подтвердила ни один вариант."))}
        for field in ("verification", "matchedQuestion"):
            if field in result:
                response[field] = result[field]
        if record:
            elapsed = round(1000 * (time.monotonic() - started))
            try:
                response["journalId"] = self.journal.record(question, options, ids, model, mode, count,
                                                           response, channel, capture_ms, elapsed,
                                                           capture_trace=data.get("captureTrace"))
            except (sqlite3.Error, OSError):
                journal_warning = "Ответ готов, но сохранить разбор на диск не удалось."
        if journal_warning:
            response["journalWarning"] = journal_warning
        return response

    def explain(self, data):
        entry = self.journal.get(data.get("id"))
        model = data.get("model", entry["model"])
        if not isinstance(model, str) or len(model) > 500:
            raise ValueError("Проверь выбранную модель.")
        index = self.meaning.wrap(self.corpus.index(entry["lectureIds"]), self.embedding_model)
        result = engine.explain_option(entry["question"], entry["options"], index, model, data.get("option"),
                                       selection_mode=entry["selectionMode"], expected_count=entry["expectedCount"],
                                       base_url=ENDPOINT, timeout=14)
        return {**result, "excerpt": result["quote"]}

    def server_close(self):
        if hasattr(self, "native"):
            self.native.close()
        super().server_close()


class ClientDisconnected(Exception):
    """The client left while uploading its HTTP request."""


def client_disconnected(error):
    return (isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.timeout))
            or error.errno in {errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT}
            or getattr(error, "winerror", None) in {10053, 10054, 10060})


def log_request_error(path, code, error):
    if code == 404:
        return
    if path in {"/api/models", "/api/embedding-models", "/api/visual/models", "/api/advisor/benchmark"}:
        area, hint = "Модель", "Проверь подключение к локальному серверу моделей."
    elif path in {"/api/ocr", "/api/detect-layout", "/api/components/ocr", "/api/native/start"}:
        area, hint = "Захват", "Открой проверку распознавания в приложении."
    elif path in {"/api/import", "/api/lectures/delete", "/api/lectures/preview"}:
        area, hint = "Документы", "Открой список источников и проверь сообщение приложения."
    else:
        area, hint = "Сервер", "Подробности доступны в окне приложения."
    if code == 500:
        hint = "Внутренняя ошибка. Для диагностики запусти с QUIZLENS_DEBUG=1."
    event(area, f"Запрос завершился с кодом HTTP {code}. {hint}", error=error,
          level="ОШИБКА" if code == 500 else "ВНИМАНИЕ", repeat_after=60)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def log_message(self, fmt, *args):
        pass

    def send(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def validate(self):
        port = self.server.server_port
        valid = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        # The public download page links to installation controls on the local homepage.
        landing = (self.command == "GET" and urlsplit(self.path).path in {"/", "/index.html"}
                   and self.headers.get("Sec-Fetch-Mode") == "navigate"
                   and self.headers.get("Sec-Fetch-Dest") == "document")
        if (host not in valid or self.headers.get("Origin") not in (None, "http://" + host)
                or (self.headers.get("Sec-Fetch-Site") == "cross-site" and not landing)):
            raise PermissionError("Запрос разрешён только из локального окна QuizLens.")

    def body(self, limit, *, upload=False):
        size = int(self.headers.get("Content-Length", "-1"))
        if not 0 < size <= limit:
            if upload and size > limit:
                raise ValueError("Документ слишком большой. До 32 МиБ на файл.")
            raise ValueError("Слишком большой или пустой файл запроса.")
        try:
            body = self.rfile.read(size)
        except OSError as exc:
            if client_disconnected(exc):
                raise ClientDisconnected from exc
            raise
        if len(body) != size:
            raise ValueError("Загрузка прервана. Выбери файл ещё раз.")
        return body

    def route(self, write=False):
        self.validate()
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if write:
            if path == "/api/import":
                name = query.get("name", [""])[0]
                if (not name or len(name) > 200 or "/" in name or "\\" in name or
                        re.search(r"[\x00-\x1f]", name) or not recognized(name)):
                    raise ValueError("Выбери PDF, PPTX, IPYNB, TXT, MD или банк .quiz.json.")
                raw = self.body(MAX_IMPORT, upload=True)
                with tempfile.TemporaryDirectory(prefix="quizlens-") as tmp:
                    upload = Path(tmp) / name
                    upload.write_bytes(raw)
                    return {"lecture": self.server.corpus.add(upload)}
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Ожидается JSON.")
            image_request = path in ("/api/ocr", "/api/detect-layout")
            byte_limit = 12 * 1024 * 1024 if image_request else 128 * 1024
            if path == "/api/visual/solve":
                byte_limit = 8 * 1024 * 1024
            data = json.loads(self.body(byte_limit))
            if not isinstance(data, dict):
                raise ValueError("Некорректный запрос приложения.")
            if path == "/api/lectures/delete":
                with self.server.preferences_lock:
                    deleted = self.server.corpus.remove(data.get("id"), self.server.meaning.forget_passages)
                    return {"deleted": deleted}
            if path == "/api/components/extension":
                if data != {"action": "open-folder"}:
                    raise ValueError("Выбери действие открытия папки.")
                return self.server.extension_folder.open()
            if path == "/api/components/ocr":
                if data != {"action": "install"}:
                    raise ValueError("Выбери действие установки компонентов.")
                return self.server.components.install()
            if path == "/api/update":
                return self.server.updates.command(data)
            if path == "/api/advisor":
                return model_advice(data)
            if path == "/api/advisor/benchmark":
                return model_bench.run_case(data.get("model"), data.get("caseIndex"), base_url=ENDPOINT)
            if path == "/api/semantic":
                model = data.get("model", "")
                if not isinstance(model, str) or len(model) > 500:
                    raise ValueError("Некорректное имя модели.")
                with self.server.preferences_lock:
                    status = {"ready": False}
                    if model.strip():
                        all_ids = [item["id"] for item in self.server.corpus.list()]
                        status = self.server.meaning.prepare(self.server.corpus.index(all_ids), model.strip())
                    target = self.server.settings_file
                    pending = target.with_suffix(".tmp")
                    pending.write_text(json.dumps({"model": model.strip()}), encoding="utf-8")
                    pending.replace(target)
                    self.server.embedding_model = model.strip()
                    return status
            if image_request:
                index = self.server.corpus.index(data["lectureIds"]) if data.get("lectureIds") else None
                vocabulary = index.ocr_vocabulary if index is not None and sys.platform != "darwin" else None
                result = ocr.reviewed_capture(data.get("image"), data.get("regions") if path == "/api/ocr" else None,
                                              vocabulary, auto=path == "/api/detect-layout")
                if "selectionMode" not in result and result["texts"]:
                    question, *options = result["texts"]
                    result.update(quiz_mode.selection_hint(question, options))
                    result["texts"] = [question, *quiz_mode.clean_options(options)]
                if result["texts"] and index is not None:
                    result["comparisonTexts"] = index.normalize_ocr_fields(result["texts"])
                return result
            if path == "/api/solve":
                return self.server.solve(data)
            if path == "/api/journal/correct":
                return {"entry": self.server.journal.save_correction(data.get("id"), data.get("choices"))}
            if path == "/api/journal/delete":
                return self.server.journal.delete(data.get("id"))
            if path == "/api/journal/forget":
                return {"entry": self.server.journal.forget(data.get("id"))}
            if path == "/api/journal/explain":
                return self.server.explain(data)
            if path == "/api/train/generate":
                ids = data.get("lectureIds")
                model = data.get("model")
                if not isinstance(ids, list) or len(ids) != 1:
                    raise ValueError("Выбери одну лекцию для подготовки вопросов.")
                if not isinstance(model, str) or not 0 < len(model.strip()) <= 500:
                    raise ValueError("Выбери локальную модель для подготовки вопросов.")
                return training.generate(self.server.corpus.index(ids), model.strip(), base_url=ENDPOINT, timeout=35)
            if path == "/api/visual/solve":
                return visual.solve(data, base_url=ENDPOINT, timeout=18)
            if path.startswith("/api/native/"):
                return self.server.native.post(path, data)
            raise FileNotFoundError(path)
        if path == "/download/QuizLens-Extension.zip":
            return ("application/zip", extension_package.extension_archive(self.server.extension_folder.source))
        if path == "/api/components/ocr":
            return self.server.components.snapshot()
        if path == "/api/ocr-status":
            return ocr.warmup_status()
        if path == "/api/update":
            return self.server.updates.snapshot()
        if path == "/api/health":
            return {"app": "QuizLens", "version": VERSION, "distribution": release_links(), "nativeCapture": sys.platform == "darwin",
                    "platform": {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, "unknown")}
        if path == "/api/advisor":
            return model_advice()
        if path == "/api/lectures":
            return {"lectures": self.server.corpus.list()}
        if path == "/api/lectures/preview":
            page = query.get("page", ["1"])[0]
            if not re.fullmatch(r"[1-9][0-9]{0,8}", page):
                raise ValueError("Выбери страницу документа.")
            return self.server.corpus.preview(query.get("id", [""])[0], int(page))
        if path == "/api/semantic":
            return {"model": self.server.embedding_model}
        if path == "/api/embedding-models":
            return {"models": semantic.list_embedding_models(ENDPOINT, timeout=3)}
        if path == "/check-questions.json":
            return ("application/json", (ROOT / "ui" / "check-questions.json").read_bytes())
        if path == "/api/models":
            return {"models": engine.list_models(base_url=ENDPOINT, timeout=3)}
        if path == "/api/journal":
            return {"entries": self.server.journal.list()}
        if path == "/api/journal/entry":
            return {"entry": self.server.journal.get(query.get("id", [""])[0])}
        if path == "/api/train":
            return training.builtin()
        if path == "/api/visual/exercises":
            return {"items": visual.exercises()}
        if path == "/api/visual/models":
            return {"models": visual.vision_models(base_url=ENDPOINT, timeout=3)}
        if path.startswith("/api/native/"):
            return self.server.native.get(path, query)
        if path == "/analytics-demo.quiz.json":
            return ("application/json", (ROOT / "examples" / path[1:]).read_bytes())
        aliases = {"/": "index.html", "/demo": "demo.html", "/native": "native.html", "/check": "check.html",
                   "/journal": "journal.html", "/train": "train.html", "/visual": "visual.html", "/setup": "setup.html"}
        filename = aliases.get(path, path.lstrip("/"))
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.(?:html|js|css|ico|png|svg)", filename):
            raise FileNotFoundError(path)
        return (WEB_TYPES[Path(filename).suffix], (ROOT / "ui" / filename).read_bytes())

    def dispatch(self, write=False):
        path = ""
        code, content_type = 200, "application/json; charset=utf-8"
        try:
            path = urlsplit(self.path).path
            track = write and path != "/api/update" and hasattr(self.server, "updates")
            with self.server.updates.work() if track else nullcontext():
                result = self.route(write)
            if isinstance(result, tuple):
                content_type, result = result
            elif write:
                if path == "/api/import":
                    event("Документы", "Документ доступен в списке источников.")
                elif path == "/api/lectures/delete" and result.get("deleted"):
                    event("Документы", "Источник удалён.")
                elif path == "/api/native/start":
                    event("Захват", "Начато чтение окна средствами macOS.")
                elif path == "/api/native/stop":
                    event("Захват", "Чтение окна остановлено.")
            if not isinstance(result, bytes):
                result = json.dumps(result, ensure_ascii=False).encode()
        except ClientDisconnected:
            self.close_connection = True
            return
        except Exception as exc:
            if isinstance(exc, FileNotFoundError):
                code, message = 404, "Страница не найдена."
            elif isinstance(exc, PermissionError):
                code, message = 403, str(exc)
            elif isinstance(exc, (ValueError, KeyError, TypeError)):
                code, message = 400, str(exc)
            elif isinstance(exc, RuntimeError):
                code, message = 503, str(exc)
            else:
                code, message = 500, "Внутренняя ошибка QuizLens. Подробности в окне запуска."
            content_type = "application/json; charset=utf-8"
            result = json.dumps({"error": message}, ensure_ascii=False).encode()
            log_request_error(path, code, exc)
        try:
            self.send(code, result, content_type)
        except OSError as exc:
            # The response may already have headers or part of its body on the wire.
            self.close_connection = True
            if not client_disconnected(exc):
                event("Сервер", "Не удалось передать ответ браузеру. Повтори действие.",
                      level="ОШИБКА", error=exc, repeat_after=60)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)


def open_ui(url):
    if sys.platform == "darwin":
        for browser in ("Google Chrome", "Microsoft Edge"):
            if subprocess.run(["/usr/bin/open", "-a", browser, url], capture_output=True).returncode == 0:
                return
    webbrowser.open(url)


def launch_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=user_dir())
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main():
    args = launch_arguments()
    try:
        server = QuizServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE and getattr(error, "winerror", None) != 10048:
            raise
        url = f"http://127.0.0.1:{args.port}"
        probe = HTTPConnection("127.0.0.1", args.port, timeout=2)
        try:
            probe.request("GET", "/api/health")
            with probe.getresponse() as response:
                existing = json.loads(response.read(1024)) if response.status == 200 else None
        except (OSError, HTTPException, ValueError):
            existing = None
        finally:
            probe.close()
        if isinstance(existing, dict) and existing.get("app") == "QuizLens":
            if existing.get("version") != VERSION:
                reported = str(existing.get("version", ""))
                running = reported if re.fullmatch(r"[0-9.]{1,20}", reported) else "другой версии"
                raise SystemExit(
                    f"На порту {args.port} уже запущен QuizLens {running}. "
                    f"Для запуска QuizLens {VERSION} нажми Ctrl+C в окне Терминала предыдущего запуска "
                    "и снова открой приложение."
                )
            event("Сервер", f"QuizLens {VERSION} уже работает · {url}")
            if not args.no_browser:
                open_ui(url)
            return
        raise SystemExit("Порт занят. Запусти с параметром --port 8766.")
    try:
        runtime = args.data_dir / "runtime"
        server.components = components.OCRComponents(runtime)
        folder = ROOT / "browser-extension"
        if not folder.is_dir():
            folder = ROOT.parent / "browser-extension"
        server.extension_folder = components.ExtensionFolder(runtime, folder)
        if server.extension_folder.target.is_dir():
            try:
                server.extension_folder.sync()
            except OSError as exc:
                event("Расширение", "Не удалось обновить файлы расширения. Открой его папку через QuizLens.",
                      level="ВНИМАНИЕ", error=exc)
        server.corpus = Corpus(args.data_dir / "lectures")
        server.journal = Journal(args.data_dir)
        server.meaning = semantic.SemanticStore(args.data_dir, ENDPOINT)
        server.preferences_lock = threading.Lock()
        server.settings_file = args.data_dir / "settings.json"
        server.embedding_model = ""
        if server.settings_file.is_file():
            try:
                value = json.loads(server.settings_file.read_text(encoding="utf-8")).get("model")
                if isinstance(value, str):
                    server.embedding_model = value
            except (ValueError, OSError, AttributeError):
                pass
        server.native = NativeMode(server.solve, server.corpus.index)
        server.updates = updater.Updates(VERSION, server.shutdown, lambda: server.components.is_installing() or server.native.is_active())
        ocr.start_prewarm()
        url = f"http://127.0.0.1:{server.server_port}"
        event("Сервер", f"QuizLens {VERSION} · {url}")
        event("Сервер", "Оставь это окно открытым. Для остановки нажми Ctrl+C.")
        if sys.platform == "win32":
            def report_ocr():
                status = server.components.snapshot()
                if status["status"] == "ready":
                    event("OCR", "Распознавание готово. Доступны русский и английский.")
                else:
                    event("OCR", "Проверь компоненты распознавания в QuizLens.", level="ВНИМАНИЕ")
            threading.Thread(target=report_ocr, name="quizlens-ocr-status", daemon=True).start()
        if not args.no_browser:
            threading.Thread(target=open_ui, args=(url,), daemon=True).start()
        server.updates.ready(server.server_port)
        server.serve_forever()
        return updater.RESTART if server.updates.restarting else 0
    except KeyboardInterrupt:
        pass
    finally:
        ocr.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
