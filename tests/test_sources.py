"""Document management operates only on QuizLens-owned copies."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from app import Corpus
from semantic import SemanticStore, _key


class SourceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.corpus = Corpus(self.root / 'library')
        self.embeddings = SemanticStore(self.root / 'cache')

    def add(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding='utf-8')
        return self.corpus.add(path)['id']

    def test_preview_exposes_all_fragments_with_locations_in_original_order(self):
        cells = [{'cell_type': 'markdown', 'source': [f'Заметка {n}.']} for n in range(6)]
        key = self.add('тренировка.ipynb', json.dumps({'cells': cells}, ensure_ascii=False))
        first = self.corpus.preview(key, 1)
        self.assertEqual((first['count'], first['pages'], first['page']), (6, 2, 1))
        self.assertEqual(len(first['sections']), 5)
        self.assertEqual(self.corpus.preview(key, 2)['sections'], [{'location': 'ячейка 6', 'text': 'Заметка 5.'}])
        with self.assertRaises(ValueError):
            self.corpus.preview(key, 3)

    def test_delete_removes_local_copy_and_index_preserves_original_and_shared_vectors(self):
        shared = 'У обоих документов есть этот фрагмент.'
        unique = 'Этот фрагмент есть только у первого документа.'
        self.add('общий.txt', shared)
        cells = [{'cell_type': 'markdown', 'source': [body]} for body in [shared, unique]]
        key = self.add('семинар.ipynb', json.dumps({'cells': cells}))
        self.assertEqual(len(self.corpus.index([key]).sections), 2)
        with closing(sqlite3.connect(self.embeddings.path)) as db, db:
            db.executemany('INSERT INTO embedding VALUES (?,?,?)',
                           [('test', _key(text), b'') for text in [shared, unique]])
        self.assertTrue(self.corpus.remove(key, self.embeddings.forget_passages))
        self.assertFalse(self.corpus.remove(key, self.embeddings.forget_passages))
        self.assertTrue((self.root / 'семинар.ipynb').is_file())
        self.assertFalse((self.corpus.folder / (key + '.json')).exists())
        self.assertNotIn(key, {row['id'] for row in Corpus(self.corpus.folder).list()})
        with self.assertRaises(ValueError):
            self.corpus.index([key])
        with self.assertRaises(ValueError):
            self.corpus.preview(key, 1)
        with closing(sqlite3.connect(self.embeddings.path)) as db:
            self.assertEqual(db.execute('SELECT digest FROM embedding').fetchall(), [(_key(shared),)])

    def test_demo_documents_can_be_previewed_and_remain_available_on_restart(self):
        demo = next(row for row in self.corpus.list() if row['builtIn'])
        self.assertTrue(self.corpus.preview(demo['id'], 1)['sections'])
        with self.assertRaisesRegex(ValueError, 'пример'):
            self.corpus.remove(demo['id'], self.embeddings.forget_passages)
        other = Corpus(self.corpus.folder)
        self.assertTrue(other.preview(demo['id'], 1)['builtIn'])

    def test_removal_can_be_retried_if_semantic_cleanup_fails(self):
        key = self.add('тезисы.txt', 'Текст конспекта для поиска.')
        with self.assertRaises(sqlite3.OperationalError):
            self.corpus.remove(key, Mock(side_effect=sqlite3.OperationalError('busy')))
        self.assertTrue((self.corpus.folder / (key + '.json')).is_file())
        self.assertTrue(self.corpus.preview(key, 1)['sections'])
        self.assertTrue(self.corpus.remove(key, self.embeddings.forget_passages))
