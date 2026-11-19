"""Local capture history and user-confirmed answers."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import unicodedata
import uuid


def _text(value):
    # Preserve case, signs, counts and punctuation. They can change quiz answers.
    return unicodedata.normalize("NFC", value.strip())


def _scope(question, options, lecture_ids, selection_mode, expected_count):
    texts = [_text(x) for x in options]
    if (not lecture_ids or not all(texts) or len(texts) != len(set(texts))
            or selection_mode not in ("single", "multiple")):
        return None
    key = [_text(question), sorted(texts), sorted(set(lecture_ids)), selection_mode,
           1 if selection_mode == "single" else expected_count]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def _timing(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return round(value)


def _capture_trace(observations):
    if not isinstance(observations, list):
        return []
    frames = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        age = item.get("ageMs")
        status = item.get("status")
        question = item.get("question")
        options = item.get("options")
        if (type(age) not in (int, float) or not 0 <= age <= 6000
                or status not in ("complete", "partial", "unstable") or not isinstance(question, str)
                or not isinstance(options, list) or any(not isinstance(s, str) for s in options)):
            continue
        stem = " ".join(question.split())[:500]
        values = [" ".join(s.split())[:350] for s in options[:8]]
        if status == "complete" and (not stem or len(values) < 2 or not all(values)):
            status = "partial"
        frames.append({"ageMs": round(age), "status": status, "question": stem, "options": values})

    recent = []
    previous = None
    frames.sort(key=lambda row: row["ageMs"], reverse=True)
    for frame in reversed(frames):
        observed = (frame["status"], frame["question"], frame["options"])
        if observed != previous:
            recent.append(frame)
            if len(recent) == 4:
                break
        previous = observed
    return recent[::-1]


def _selected(row, saved_text):
    if not saved_text:
        return []
    answers = set(json.loads(saved_text))
    return [i for i, value in enumerate(row["options"], 1) if _text(value) in answers]


class Journal:
    HISTORY_LIMIT = 200
    CARD_LIMIT = 1000

    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "journal.sqlite3"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS captures ("
                       "seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, scope TEXT, "
                       "created TEXT NOT NULL, question TEXT NOT NULL, channel TEXT NOT NULL, "
                       "answered INTEGER NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS corrections ("
                       "revision INTEGER PRIMARY KEY, scope TEXT UNIQUE NOT NULL, answers TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def record(self, question, options, lecture_ids, model, selection_mode, expected_count,
               response, channel, capture_ms, solve_ms, capture_trace=None):
        key = _scope(question, options, lecture_ids, selection_mode, expected_count)
        identifier = uuid.uuid4().hex
        created = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        data = {k: response[k] for k in ("choice", "choices", "source", "kind", "excerpt", "evidence", "detail")
                if k in response}
        row = {"id": identifier, "createdAt": created, "question": question, "options": list(options),
               "lectureIds": list(lecture_ids), "model": model, "selectionMode": selection_mode,
               "expectedCount": expected_count, "response": data, "channel": channel,
               "captureMs": _timing(capture_ms), "solveMs": _timing(solve_ms),
               "captureTrace": _capture_trace(capture_trace)}
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO captures(id,scope,created,question,channel,answered,body) "
                       "VALUES (?,?,?,?,?,?,?)", (identifier, key, created, question, channel,
                       bool(data.get("choices") or data.get("choice")), json.dumps(row, ensure_ascii=False)))
            db.execute("DELETE FROM captures WHERE seq <= (SELECT seq FROM captures "
                       "ORDER BY seq DESC LIMIT 1 OFFSET ?)", (self.HISTORY_LIMIT,))
        return identifier

    def list(self):
        with closing(self._connect()) as db:
            rows = db.execute("SELECT c.id,c.created,c.question,c.channel,c.answered,k.scope FROM captures c "
                              "LEFT JOIN corrections k ON k.scope=c.scope ORDER BY c.seq DESC").fetchall()
        return [{"id": ident, "createdAt": created, "question": question, "channel": channel,
                 "answered": bool(answered), "corrected": scope is not None}
                for ident, created, question, channel, answered, scope in rows]

    def _entry(self, db, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32:
            raise ValueError("Выбери запись в журнале.")
        result = db.execute("SELECT c.body,c.scope,k.answers FROM captures c "
                            "LEFT JOIN corrections k ON k.scope=c.scope WHERE c.id=?", (identifier,)).fetchone()
        if not result:
            raise ValueError("Запись больше не доступна. Открой свежую запись в журнале.")
        body, scope, answers = result
        row = json.loads(body)
        row.setdefault("captureTrace", [])
        selected = _selected(row, answers)
        row["correctionChoices"] = selected
        row["correctionTexts"] = [row["options"][i - 1] for i in selected]
        return row, scope

    def get(self, identifier):
        with closing(self._connect()) as db:
            return self._entry(db, identifier)[0]

    def save_correction(self, identifier, choices):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row, scope = self._entry(db, identifier)
            if scope is None:
                raise ValueError("Проверь распознавание вариантов и режим вопроса перед сохранением карточки.")
            if (not isinstance(choices, list) or not choices or
                    any(type(i) is not int or not 1 <= i <= len(row["options"]) for i in choices)
                    or len(set(choices)) != len(choices)):
                raise ValueError("Отметь правильные варианты из этого вопроса.")
            count = 1 if row["selectionMode"] == "single" else row["expectedCount"]
            if count is not None and len(choices) != count:
                raise ValueError(f"Для этого вопроса нужно отметить ровно {count}.")
            answers = sorted(_text(row["options"][i - 1]) for i in choices)
            # A single current correction applies across all reordered captures of this card.
            db.execute("INSERT OR REPLACE INTO corrections(scope,answers) VALUES(?,?)",
                       (scope, json.dumps(answers, ensure_ascii=False)))
            db.execute("DELETE FROM corrections WHERE revision <= (SELECT revision FROM corrections "
                       "ORDER BY revision DESC LIMIT 1 OFFSET ?)", (self.CARD_LIMIT,))
            return self._entry(db, identifier)[0]

    def lookup(self, question, options, lecture_ids, selection_mode, expected_count):
        scope = _scope(question, options, lecture_ids, selection_mode, expected_count)
        if not scope:
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT answers FROM corrections WHERE scope=?", (scope,)).fetchone()
        if not row:
            return None
        choices = _selected({"options": options}, row[0])
        if not choices or (expected_count is not None and len(choices) != expected_count):
            return None
        source = "Моя карточка · журнал QuizLens"
        evidence = [{"choice": i, "source": source, "quote": options[i - 1]} for i in choices]
        return {"choice": choices[0] if len(choices) == 1 else None, "choices": choices, "kind": "correction",
                "source": source, "quote": evidence[0]["quote"], "evidence": evidence}

    def forget(self, identifier):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _, scope = self._entry(db, identifier)
            db.execute("DELETE FROM corrections WHERE scope=?", (scope,))
            return self._entry(db, identifier)[0]

    def delete(self, identifier):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._entry(db, identifier)
            db.execute("DELETE FROM captures WHERE id=?", (identifier,))
        return {"deleted": True}
