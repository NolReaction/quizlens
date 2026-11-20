"""Local macOS window capture and quiz session lifetime management."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Condition, RLock, Thread
import time
import unicodedata

from auto_layout import assess_layout
from ocr_layout import ocr_texts
from ocr import vision_reading_warnings
from quiz_mode import clean_options, effective_hint, selection_hint


# Background tabs can receive timer callbacks only once per minute.
SESSION_LEASE_SECONDS = 75
# Manual and full-window observations arrive at different cadences.
UNSETTLED_SECONDS = 1.2


def _note_capture(session, texts, complete, now):
    question = (texts[0] if texts else "")[:400]
    options = tuple(text[:220] for text in texts[1:9])
    observation = ("complete" if complete else "partial", question, options)
    trace = session["captureTrace"]
    trace[:] = [(stamp, value) for stamp, value in trace if now - stamp <= 4]
    if trace and trace[-1][1] == observation:
        return
    trace[:] = [(stamp, value) for stamp, value in trace if value != observation]
    trace.append((now, observation))
    del trace[:-4]


def _trace_for_request(session, now):
    return [{"ageMs": round(1000 * max(0, now - stamp)), "status": status,
             "question": question, "options": list(options)}
            for stamp, (status, question, options) in session["captureTrace"] if now - stamp <= 4]


def _window_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Выберите окно квиза.") from None
    if not 0 < number <= 0xFFFFFFFF:
        raise ValueError("Недействительный номер окна.")
    return number


def _query_value(query, name):
    value = query.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _requested_mode(value):
    if value not in ("auto", "single", "multiple"):
        raise ValueError("Выбери режим ответа: авто, один или несколько.")
    return value


def _rectangle(entry):
    if not isinstance(entry, list) or len(entry) != 4:
        raise ValueError("Выдели прямоугольную область.")
    if not all(type(n) in (int, float) and math.isfinite(n) for n in entry):
        raise ValueError("Проверь координаты выбранных областей.")
    x, y, w, h = entry
    if min(x, y) < 0 or min(w, h) < .002 or x + w > 1.000001 or y + h > 1.000001:
        raise ValueError("Область текста выходит за границы окна.")
    return entry


def _regions(value):
    if not isinstance(value, list) or not 3 <= len(value) <= 9:
        raise ValueError("Выдели вопрос и от двух до восьми вариантов.")
    return [_rectangle(entry) for entry in value]


def _inside_focus(lines, focus):
    left, top, width, height = focus
    inside = []
    for line in lines:
        x, y, w, h = (line[k] for k in ("x", "y", "w", "h"))
        if not (left <= x + w/2 <= left + width and top <= y + h/2 <= top + height):
            continue
        x0, x1 = max(left, x), min(left + width, x + w)
        y0, y1 = max(top, y), min(top + height, y + h)
        if x1 > x0 and y1 > y0:
            inside.append(dict(line, x=(x0-left)/width, y=(y0-top)/height,
                               w=(x1-x0)/width, h=(y1-y0)/height))
    return inside


class NativeMode:
    def __init__(self, solve_fn, index_fn):
        self._solve = solve_fn
        self._index = index_fn
        self._condition = Condition(RLock())
        self._session = None
        self._pending = None
        self._closed = False
        self._threads_started = False

    @staticmethod
    def _executable():
        binary = Path(os.environ.get("QUIZLENS_WINDOW_READER") or Path(__file__).resolve().parent / "native" / "WindowReader")
        if sys.platform != "darwin":
            raise RuntimeError("Захват выбранного окна доступен на Mac с macOS 14 или новее.")
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError("Модуль WindowReader отсутствует. Повторите запуск приложения через комплектный скрипт.")
        return str(binary)

    def _run_once(self, arguments):
        try:
            result = subprocess.run(
                [self._executable(), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, timeout=45, check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("macOS ожидает разрешение на запись экрана. Проверьте Системные настройки и повторите.") from None
        if result.returncode:
            error = result.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(error[-800:] or "Не удалось получить окно. Проверьте разрешение macOS.")
        return result.stdout

    def is_active(self):
        with self._condition:
            return self._session is not None and self._session["state"] != "error"

    def get(self, path, query):
        if path == "/api/native/windows":
            return {"windows": json.loads(self._run_once(["--list"]))}
        if path == "/api/native/preview":
            number = _window_id(_query_value(query, "id"))
            return ("image/png", self._run_once(["--preview", str(number)]))
        if path == "/api/native/status":
            token = _query_value(query, "session")
            with self._condition:
                session = self._session
                if session is None or token != session["id"]:
                    return {"state": "ended", "message": "Захват завершён. Для продолжения нажмите «Начать»."}
                session["heartbeat"] = time.monotonic()
                result = {
                    name: session[name] for name in
                    ("state", "message", "question", "options", "answer", "elapsed", "revision",
                     "requestedMode", "selectionMode", "expectedCount", "readingWarnings", "canConfirmReading")
                }
                return result
        raise ValueError("Неизвестная команда захвата.")

    def post(self, path, payload):
        if path == "/api/native/start":
            return self._start(payload)
        if path == "/api/native/confirm-reading":
            with self._condition:
                session = self._session
                if (not session or session["id"] != payload.get("session") or session["state"] != "review"
                        or type(payload.get("revision")) is not int or payload["revision"] != session["revision"]
                        or not session["canConfirmReading"]):
                    raise ValueError("Кадр уже изменился. Сверь текущий вопрос.")
                edits = payload.get("edits")
                fields = {w["field"] for w in session["readingWarnings"]}
                if not isinstance(edits, list) or len(edits) != len(fields):
                    raise ValueError("Подтверди каждое спорное поле.")
                corrected = list(session["reviewSnapshot"][0])
                for edit in edits:
                    if not isinstance(edit, dict) or type(edit.get("field")) is not int or edit["field"] not in fields:
                        raise ValueError("Неизвестное поле для исправления.")
                    field = edit["field"]
                    value = edit.get("text")
                    if not isinstance(value, str) or not value.strip() or len(value) > max(4096, len(corrected[field])):
                        raise ValueError("Проверь длину исправленного текста.")
                    fields.remove(field)
                    corrected[field] = " ".join(unicodedata.normalize("NFC", value).split())
                if len(set(corrected[1:])) != len(corrected) - 1:
                    raise ValueError("Варианты ответа должны различаться.")
                session.update(confirmedReading=corrected, canConfirmReading=False,
                               message="Проверяю, что на экране тот же вопрос…")
            return {"ok": True}
        if path in ("/api/native/mode", "/api/native/refresh"):
            with self._condition:
                session = self._session
                if not session or session["id"] != payload.get("session") or session["state"] == "error":
                    raise ValueError("Сессия захвата завершена.")
                requested = _requested_mode(payload.get("selectionMode", session["requestedMode"]))
                if requested != session["requestedMode"] or path == "/api/native/refresh":
                    # Invalidate any in-flight solve before confirming the next frames.
                    self._pending = None
                    session.update(requestedMode=requested, selectionMode=None, expectedCount=None,
                                   revision=session["revision"] + 1, signature=None, recent=[], captureTrace=[],
                                   unsettledSince=None, held=None, answer=None, elapsed=0, readingWarnings=[],
                                   canConfirmReading=False, reviewSnapshot=None, confirmedReading=None,
                                   question="", options=[], state="waiting",
                                   message="Проверяем режим ответа для текущего вопроса…")
            return {"requestedMode": requested}
        if path == "/api/native/stop":
            with self._condition:
                session = self._session
                if session and session["id"] == payload.get("session"):
                    self._stop_locked()
            return {"state": "ended"}
        raise ValueError("Неизвестная команда захвата.")

    def _start(self, payload):
        number = _window_id(payload.get("id"))
        requested = _requested_mode(payload.get("selectionMode", "auto"))
        auto = payload.get("auto", False)
        if type(auto) is not bool:
            raise ValueError("Проверьте режим захвата окна.")
        record = payload.get("record", False)
        if type(record) is not bool:
            raise ValueError("Проверь настройку журнала.")
        focus = None
        focus_aspect = None
        if "focus" in payload:
            focus = _rectangle(payload["focus"])
            focus_aspect = payload.get("focusAspect")
            if (not auto or type(focus_aspect) not in (float, int) or not math.isfinite(focus_aspect)
                    or focus_aspect <= 0):
                raise ValueError("Обнови кадр и выдели рамку заново.")
        field_count = 0
        arguments = ["--watch", str(number)]
        if not auto:
            regions = _regions(payload.get("regions"))
            field_count = len(regions)
            arguments += ["--regions", json.dumps(regions, separators=(",", ":"))]
        model = payload.get("model", "")
        lecture_ids = payload.get("lectureIds", [])
        if not isinstance(model, str) or not isinstance(lecture_ids, list) or not all(isinstance(i, str) for i in lecture_ids):
            raise ValueError("Проверьте выбор модели и лекций.")
        binary = self._executable()
        index = self._index(lecture_ids)
        now = time.monotonic()
        with self._condition:
            if self._closed:
                raise RuntimeError("Приложение завершает работу.")
            active = self._session
            if active and active["state"] != "error" and now - active["heartbeat"] < SESSION_LEASE_SECONDS:
                raise ValueError("В другом окне приложения уже идёт захват. Сначала остановите ту сессию.")
            self._stop_locked()
            proc = subprocess.Popen(
                [binary, *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            session = {
                "id": secrets.token_urlsafe(20), "process": proc, "state": "waiting",
                "auto": auto, "requestedMode": requested, "selectionMode": None, "expectedCount": None,
                "captureTrace": [], "focus": focus, "focusAspect": focus_aspect, "fieldCount": field_count, "record": record,
                "message": "Ищу вопрос и варианты…" if auto else "Ожидание текста в выбранных областях…",
                "question": "", "options": [], "readingWarnings": [],
                "canConfirmReading": False, "reviewSnapshot": None, "confirmedReading": None,
                "answer": None, "elapsed": 0, "heartbeat": now, "started": now, "lastFrame": None, "revision": 0,
                "signature": None, "recent": [], "unsettledSince": None, "held": None, "changed": now,
                "normalize": index.normalize_ocr_fields,
                "model": model, "lectureIds": tuple(lecture_ids),
            }
            self._session = session
            if not self._threads_started:
                self._threads_started = True
                Thread(target=self._watchdog, daemon=True).start()
                # A changed question can start while the previous HTTP request finishes.
                for _ in range(2):
                    Thread(target=self._solve_worker, daemon=True).start()
            Thread(target=self._read_frames, args=(session,), daemon=True).start()
            return {"session": session["id"]}

    def _read_frames(self, session):
        process = session["process"]
        error = "Программа захвата остановилась. Проверьте окно и разрешения macOS."
        try:
            with process.stdout as output:
                for line in output:
                    frame = json.loads(line)
                    if frame.get("type") == "error":
                        error = str(frame.get("message") or error)
                        break
                    if frame.get("type") == "frame":
                        self._handle_frame(session, frame["lines"], frame.get("aspect", 1.6))
        except (OSError, ValueError, KeyError, TypeError):
            error = "Не удалось прочитать кадр. Повторно выберите окно."
        finally:
            with self._condition:
                if self._session is session and session["state"] != "error":
                    self._error_locked(session, error)
            # Reap children even when another session has replaced this one.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _handle_frame(self, session, lines, aspect=1.6):
        issue = None
        if session["auto"]:
            focus = session["focus"]
            if focus:
                if abs(aspect / session["focusAspect"] - 1) > .04:
                    issue = "Размер окна изменился. Обнови рамку поиска или верни автозахват всего окна."
                    lines = []
                else:
                    lines = _inside_focus(lines, focus)
                    aspect = aspect * focus[2] / focus[3]
            layout = assess_layout(lines, aspect=aspect)
            texts = layout["texts"]
            hint = layout
            if not texts and layout.get("captureStatus") == "partial":
                issue = issue or layout.get("hint")
        else:
            parts = [[] for _ in range(session["fieldCount"])]
            for line in lines:
                index = line["region"]
                if type(index) == int and 0 <= index < len(parts):
                    parts[index].append(line)
            texts = ocr_texts(parts)
            hint = selection_hint(texts[0], texts[1:])
            texts[1:] = clean_options(texts[1:])
        warnings = vision_reading_warnings(lines, texts, layout["regions"] if session["auto"] else None)
        fields = None
        if 3 <= len(texts) <= 9 and all(texts):
            fields = tuple(unicodedata.normalize("NFC", s) for s in session["normalize"](texts))
        with self._condition:
            if self._session is not session or session["state"] == "error":
                return
            signature = None
            if fields is not None:
                resolved = effective_hint(hint, session["requestedMode"])
                mode, count = resolved["selectionMode"], resolved["expectedCount"]
                signature = (fields, mode, count)
            now = time.monotonic()
            recent = session["recent"]
            session["lastFrame"] = now
            frame_key = (tuple(texts), signature[1:] if signature is not None else None)
            snapshot = (*frame_key, tuple((w["field"], tuple(w["readings"])) for w in warnings))
            prior = session["reviewSnapshot"]
            approved = session["confirmedReading"] is not None and prior is not None and (
                snapshot == prior or (not warnings and frame_key == prior[:2]))
            if approved:
                texts = session["confirmedReading"]
                fields = tuple(texts)
                signature = (fields, mode, count)
                if session["readingWarnings"]:
                    # This fresh frame matched the one the user corrected.
                    recent[:] = [(signature, now)]
                    session.update(readingWarnings=[], canConfirmReading=False)
            elif warnings and fields is not None:
                repeated = prior == snapshot
                if not repeated:
                    session["revision"] += 1
                self._pending = None
                recent.clear()
                if session["record"]:
                    _note_capture(session, texts, False, now)
                session.update(state="review", message=("Сверь с изображением, при необходимости исправь текст и подтверди."
                               if repeated else "Повторно читаю выделенный фрагмент…"),
                               question=texts[0], options=texts[1:], selectionMode=mode, expectedCount=count,
                               readingWarnings=warnings, canConfirmReading=repeated, reviewSnapshot=snapshot,
                               confirmedReading=None, answer=None, elapsed=0, held=None, signature=None,
                               unsettledSince=None)
                return
            else:
                if session["confirmedReading"] is not None:
                    self._pending = None
                    session.update(revision=session["revision"] + 1, signature=None, recent=[], answer=None,
                                   state="waiting", held=None, question="", options=[], message="Проверяю новый кадр…")
                    recent = session["recent"]
                session.update(reviewSnapshot=None, canConfirmReading=False, confirmedReading=None)
            if session["record"] and (fields is not None or any(texts) or issue or session["signature"]):
                _note_capture(session, texts, fields is not None, now)
            if session["readingWarnings"]:
                # Require two consecutive clean reads of this question before releasing the warning.
                if signature is not None and (not recent or recent[-1][0] != signature):
                    recent[:] = [(signature, now)]
                    return
                session.update(state="waiting", message="Перепроверяю текст…", question="", options=[],
                               readingWarnings=[], selectionMode=None, expectedCount=None)
            if signature is not None and session["signature"] == signature:
                recent.clear()
                session["unsettledSince"] = None
                if session["held"] is not None:
                    session.update(session["held"])
                    session["held"] = None
                return
            if session["unsettledSince"] is None:
                session["unsettledSince"] = now
            recent.append((signature, now))
            del recent[:-3]
            matches = [stamp for value, stamp in recent if value == signature]
            if len(matches) < 2:
                # Never leave a previous choice up indefinitely while a different
                # question is animating or the marked regions have moved.
                if session["signature"] is None or now - session["unsettledSince"] < UNSETTLED_SECONDS:
                    return
                signature = None
            if signature == session["signature"]:
                if signature is None and session["state"] == "waiting" and len(matches) >= 2:
                    session["message"] = issue or "Ожидаю вопрос и варианты…"
                return
            session.update(signature=signature, revision=session["revision"] + 1, held=None,
                           unsettledSince=None, answer=None, elapsed=0)
            self._pending = None
            if signature is None:
                session.update(state="waiting", question="", options=[],
                               selectionMode=None, expectedCount=None,
                               message=issue or "Ожидаю вопрос и варианты…")
                return
            recent.clear()
            session.update(changed=matches[0], question=texts[0], options=texts[1:],
                           selectionMode=mode, expectedCount=count)
            if mode == "unknown":
                session.update(state="waiting", message="Уточни режим ответа выше: «Один» или «Несколько».")
                return
            session.update(state="solving", message="Ищем ответ…")
            self._pending = (session, session["revision"], {
                "question": texts[0], "options": texts[1:], "model": session["model"],
                "selectionMode": mode, "expectedCount": count,
                "lectureIds": list(session["lectureIds"]), "record": session["record"], "channel": "native",
                "captureMs": round(1000 * max(0, now - session["changed"])),
                **({"captureTrace": _trace_for_request(session, now)} if session["record"] else {}),
            })
            self._condition.notify_all()

    def _solve_worker(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                session, revision, payload = self._pending
                self._pending = None
                dispatched_at = time.monotonic()
                payload["captureMs"] = round(1000 * max(0, dispatched_at - session["changed"]))
                if session["record"]:
                    payload["captureTrace"] = _trace_for_request(session, dispatched_at)
            try:
                answer = self._solve(payload)
                message = "" if answer.get("choices") or type(answer.get("choice")) is int and 1 <= answer["choice"] <= len(payload["options"]) else (
                    answer.get("detail") or "В материалах недостаточно данных для выбора."
                )
            except Exception as exc:
                answer, message = None, str(exc) or "Не удалось получить ответ модели."
            with self._condition:
                if self._session is session and session["revision"] == revision and session["state"] == "solving":
                    result = dict(answer=answer, state="answer", message=message,
                                  elapsed=round(time.monotonic() - session["changed"], 2))
                    if session["unsettledSince"] is None:
                        session.update(result)
                    else:
                        session["held"] = result

    def _watchdog(self):
        while True:
            with self._condition:
                if self._closed:
                    return
                session = self._session
                now = time.monotonic()
                if session:
                    if now - session["heartbeat"] > SESSION_LEASE_SECONDS:
                        self._stop_locked()
                    elif session["state"] != "error":
                        last_frame = session["lastFrame"]
                        stalled = (now - session["started"] > 60 if last_frame is None else now - last_frame > 20)
                        if stalled:
                            self._error_locked(session, "Окно перестало передавать кадры. Восстановите его и запустите захват заново.")
                self._condition.wait(timeout=1)

    def _error_locked(self, session, message):
        session.update(state="error", answer=None, question="", options=[], message=message, readingWarnings=[])
        self._pending = None
        if session["process"].poll() is None:
            session["process"].terminate()

    def _stop_locked(self):
        session, self._session = self._session, None
        self._pending = None
        if session and session["process"].poll() is None:
            session["process"].terminate()
        self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._stop_locked()
