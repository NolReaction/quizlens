"""Optional local semantic retrieval for an existing LectureIndex."""
from __future__ import annotations

from array import array
from collections import defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import math
import sqlite3
import threading
import time
import weakref

import engine


def list_embedding_models(base_url=engine.LOCAL_API, timeout=4):
    """Suggest embedding IDs. Custom aliases can also be entered in Settings."""
    response = engine._api(base_url, "models", timeout=timeout)
    data = response.get("data", []) if isinstance(response, dict) else []
    result = []
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        name = row["id"].lower()
        if row.get("type") == "embedding" or any(marker in name for marker in ("embed", "bge-", "e5-", "minilm", "gte-", "jina-v")):
            result.append(row["id"])
    return result


def _check_model(model):
    if not isinstance(model, str) or len(model) > 500 or not model.strip():
        raise ValueError("Укажите идентификатор локальной модели для поиска по смыслу.")
    return model.strip()


def _prompt(model, text, *, query):
    model = model.lower()
    if "nomic-embed-text" in model:
        return ("search_query: " if query else "search_document: ") + text
    if "e5-" in model and "instruct" not in model:
        return ("query: " if query else "passage: ") + text
    return text


def _embeddings(base_url, model, texts, *, query=False, timeout=40):
    payload = {"model": model, "input": [_prompt(model, text, query=query) for text in texts]}
    reply = engine._api(base_url, "embeddings", payload=payload, timeout=timeout)
    rows = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError("LM Studio вернул неполный набор векторов. Проверьте embedding-модель.")
    ordered = [None] * len(texts)
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Некорректный вектор от LM Studio.")
        i, values = row.get("index"), row.get("embedding")
        if type(i) is not int or not 0 <= i < len(ordered) or ordered[i] is not None:
            raise RuntimeError("Нарушен порядок векторов в ответе LM Studio.")
        if not isinstance(values, list) or not 1 <= len(values) <= 16384 or not all(
                type(x) in (float, int) and math.isfinite(x) for x in values):
            raise RuntimeError("LM Studio вернул вектор неподдерживаемого формата.")
        norm = math.sqrt(sum(x*x for x in values))
        if not math.isfinite(norm) or norm <= 0:
            raise RuntimeError("Локальная модель вернула пустой вектор. Проверьте выбранную модель.")
        ordered[i] = array('f', (x / norm for x in values))
    if len({len(vector) for vector in ordered}) != 1:
        raise RuntimeError("Модель вернула векторы разной длины.")
    return ordered


def _sections(value):
    return value.sections if hasattr(value, "sections") else value


def _key(text):
    return sha256(text.encode("utf-8")).hexdigest()


class SemanticStore:
    def __init__(self, path_folder, base_url=engine.LOCAL_API):
        directory = Path(path_folder)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "semantic-v1.sqlite3"
        self.base_url = base_url
        self._preparing = threading.Lock()
        self._state = threading.Lock()
        self._by_index = weakref.WeakKeyDictionary()
        self._unavailable_until = {}
        self._revision = 0
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS embedding (model TEXT NOT NULL, digest TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(model, digest))")
            db.commit()

    def _load(self, keys, model, *, with_vectors=False):
        results = {}
        keys = list(dict.fromkeys(keys))
        with closing(sqlite3.connect(self.path)) as db:
            for start in range(0, len(keys), 400):
                batch = keys[start:start+400]
                fields = "digest,vector" if with_vectors else "digest"
                query = f"SELECT {fields} FROM embedding WHERE model=? AND digest IN ({','.join('?' for _ in batch)})"
                for row in db.execute(query, [model, *batch]):
                    if with_vectors:
                        vector = array('f')
                        vector.frombytes(row[1])
                        results[row[0]] = vector
                    else:
                        results[row[0]] = True
        return results

    def status(self, index_or_sections, model):
        rows = _sections(index_or_sections)
        keys = [_key(row['text']) for row in rows]
        known = self._load(keys, model) if model else {}
        prepared = sum(key in known for key in keys)
        return dict(model=model, total=len(keys), prepared=prepared, missing=len(keys)-prepared,
                    ready=bool(keys) and prepared == len(keys))

    def prepare(self, index_or_sections, model):
        model = _check_model(model)
        rows = _sections(index_or_sections)
        passages = {_key(row['text']): row['text'] for row in rows}
        if not passages:
            raise ValueError("Добавьте хотя бы одну лекцию для построения индекса.")
        with self._preparing:
            present = self._load(passages, model)
            missing = [key for key in passages if key not in present]
            for start in range(0, len(missing), 4):
                batch = missing[start:start+4]
                vectors = _embeddings(self.base_url, model, [passages[key] for key in batch])
                with closing(sqlite3.connect(self.path)) as db:
                    with db:
                        db.executemany("INSERT OR REPLACE INTO embedding VALUES (?,?,?)",
                                       [(model, key, vec.tobytes()) for key, vec in zip(batch, vectors)])
                with self._state:
                    self._revision += 1
                    self._by_index.clear()
                    self._unavailable_until.pop(model, None)
        return self.status(rows, model)

    def wrap(self, index, model):
        if not model or not index.sections:
            return index
        model = _check_model(model)
        with self._state:
            revision = self._revision
            cached = self._by_index.get(index, {})
            if model in cached:
                vectors = cached[model]
                return _Hybrid(index, self, model, vectors) if vectors else index
        keys = [_key(row['text']) for row in index.sections]
        found = self._load(keys, model, with_vectors=True)
        vectors = [found[key] for key in keys] if all(k in found for k in keys) else []
        with self._state:
            if self._revision == revision:
                self._by_index.setdefault(index, {})[model] = vectors
        return _Hybrid(index, self, model, vectors) if vectors else index

    def _query_vector(self, model, question):
        with self._state:
            if time.monotonic() < self._unavailable_until.get(model, 0):
                return None
        try:
            return _embeddings(self.base_url, model, [question], query=True, timeout=1.5)[0]
        except (RuntimeError, OSError, ValueError):
            with self._state:
                self._unavailable_until[model] = time.monotonic() + 15
            return None


class _Hybrid:
    def __init__(self, index, store, model, vectors):
        self.index = index
        self.store = store
        self.model = model
        self.vectors = vectors

    def __getattr__(self, name):
        return getattr(self.index, name)

    def search(self, question, options=(), limit=4, *, accept=None):
        if limit <= 0:
            return []
        candidates = 3*limit
        lexical = self.index.search(question, options, limit=candidates, accept=accept)
        vector = self.store._query_vector(self.model, question)
        if vector is None or any(len(v) != len(vector) for v in self.vectors):
            return lexical[:limit]
        similarities = [(sum(a*b for a,b in zip(vector, embedding)), i) for i, embedding in enumerate(self.vectors)
                        if accept is None or accept(self.index.sections[i])]
        closest = sorted((entry for entry in similarities if entry[0] > 0), key=lambda it: -it[0])[:candidates]
        if not closest:
            return lexical[:limit]
        # Reciprocal rank fusion preserves useful term matches despite different score scales.
        scores = defaultdict(float)
        for rank, row in enumerate(lexical, 1):
            scores[int(row['id'][1:])-1] += 1 / (12 + rank)
        for rank, (_, idx) in enumerate(closest, 1):
            scores[idx] += 1 / (12 + rank)
        best = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [dict(self.index.sections[i], id=f'S{i+1}', score=round(scores[i], 6)) for i in best]
