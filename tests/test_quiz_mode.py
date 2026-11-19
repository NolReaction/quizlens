import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auto_layout import detect_layout
from quiz_mode import clean_options, effective_hint, instruction_only, selection_hint


SINGLE = {'selectionMode': 'single', 'expectedCount': 1}
MULTI = {'selectionMode': 'multiple', 'expectedCount': None}
TWO = {'selectionMode': 'multiple', 'expectedCount': 2}
UNKNOWN = {'selectionMode': 'unknown', 'expectedCount': None}


class ModeTests(unittest.TestCase):
    def test_modes_in_visible_questions_or_labels(self):
        cases = [
            ('Выберите два неверных утверждения о метриках.', TWO),
            ('Which two fields are metadata?', TWO),
            ('Select two true statements about recall and precision.', TWO),
            ('Выберите один или несколько ответов.', MULTI),
            ('Выберите до двух вариантов.', MULTI),
            ('Отметьте правильные ответы.', MULTI),
            ('Выберите верные утверждения.', MULTI),
            ('Select the correct answers.', MULTI),
            ('Может быть несколько правильных ответов.', MULTI),
            ('Select all that apply.', MULTI),
            ('One or more answers may be correct', MULTI),
            ('Какие утверждения верны? Отметьте все подходящие.', MULTI),
            ('Select one or more options.', MULTI),
            ('Выберите два или три ответа', MULTI),
            ('Choose any three answers.', dict(MULTI, expectedCount=3)),
            ('Select exactly 2 answers.', TWO),
            ('Отметьте один верный ответ.', SINGLE),
            ('Что делает SQL SELECT ALL?', SINGLE),
            ('Какие утверждения верны?', SINGLE),
            ('Как выбрать два файла?', SINGLE),
            ('Выберите дважды встречающееся слово.', SINGLE),
            ('2 + 2 = ?', SINGLE),
            ('What does “select two” mean?', SINGLE),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(selection_hint(text), expected)
        self.assertEqual(selection_hint('Что верно?', instructions='Правильных ответов: 2'), TWO)
        self.assertTrue(instruction_only('Выберите один или несколько вариантов ответа.'))
        self.assertFalse(instruction_only('Выберите два верных утверждения о Precision.'))

    def test_conflict_and_exact_bounds(self):
        self.assertEqual(selection_hint('Choose two answers.', ['a','b','c','d'], ['Select one.']), UNKNOWN)
        self.assertEqual(selection_hint('Choose two answers.', instructions=['Choose three.']), UNKNOWN)
        self.assertEqual(selection_hint('Choose five answers.', ['a','b','c','d']), UNKNOWN)
        self.assertEqual(selection_hint('Choose two.', ['x']), UNKNOWN)
        self.assertEqual(selection_hint('Select all that apply.', instructions=['Select two.']), TWO)

    def test_controls_can_establish_mode_without_parsing_answer_content(self):
        values = ['[ ] 12', '☑ 3.14', '☐ Choose one answer', '∑ x']
        self.assertEqual(selection_hint('What?', values), MULTI)
        self.assertEqual(selection_hint('Choose one.', values), UNKNOWN)
        self.assertEqual(selection_hint('Select a single answer.', values), UNKNOWN)
        self.assertEqual(clean_options(values), ['12', '3.14', 'Choose one answer', '∑ x'])
        self.assertEqual(clean_options(clean_options(values)), clean_options(values))
        for raw in (['x', 'y', 'z', '□ shape'], ['[1] = 1', 'A', 'B', 'D'],
                    ['1 kg', '2 GB', '3.14', '4 minutes']):
            self.assertEqual(clean_options(raw), raw)
            self.assertEqual(selection_hint('Что верно?', raw), SINGLE)
        self.assertEqual(clean_options(['OA. Publisher', 'mB. Price', 'oc. Update', 'wD. Reading']),
                         ['Publisher', 'Price', 'Update', 'Reading'])

    def test_manual_override_conflicts(self):
        self.assertEqual(effective_hint(TWO), TWO)
        self.assertEqual(effective_hint(UNKNOWN), UNKNOWN)
        self.assertEqual(effective_hint(TWO, 'single'), SINGLE)
        self.assertEqual(effective_hint(SINGLE, 'multiple'), MULTI)
        self.assertEqual(effective_hint(TWO, 'multiple'), TWO)
        self.assertEqual(effective_hint(UNKNOWN, 'multiple'), MULTI)


def part(text, x, y, w=.29, h=.025):
    return dict(text=text, x=x, y=y, w=w, h=h)


class CaptureModeTests(unittest.TestCase):
    stem = 'Какие поля описывают набор?'
    values = ['Владелец', 'Температура', 'Дата обновления', 'Цена']

    def frame(self, stem=None, boxes=False):
        parts = [part(stem or self.stem, .18, .25, .44)]
        for i, v in enumerate(self.values):
            y = .39 + .09*i
            parts.append(part(v, .23, y, .22))
            if boxes:
                parts.append(part('☑' if i == 0 else '☐', .18, y, .013))
        return parts

    def test_instruction_near_quiz_preserves_capture_rows(self):
        for y in (.19, .315, .75):
            with self.subTest(y=y):
                result = detect_layout(self.frame() + [part('Выберите ровно два ответа', .18, y)])
                self.assertEqual(result['texts'], [self.stem] + self.values)
                self.assertEqual((result['selectionMode'], result['expectedCount']), ('multiple', 2))
        result = detect_layout(self.frame() + [part('Choose two.', .84, .75, .12)])
        self.assertEqual(result['selectionMode'], 'single')

    def test_checkboxes_and_ambiguity(self):
        result = detect_layout(self.frame(boxes=True))
        self.assertEqual(result['selectionMode'], 'multiple')
        self.assertIsNone(result['expectedCount'])
        self.assertEqual(result['texts'], [self.stem] + self.values)
        conflict = detect_layout(self.frame(boxes=True) + [part('Choose one.', .2, .74)])
        self.assertEqual(conflict['selectionMode'], 'unknown')

    def test_instruction_as_stem_and_numeric_grid_content(self):
        result = detect_layout(self.frame('Выберите два неверных утверждения'))
        self.assertEqual(result['texts'][0], 'Выберите два неверных утверждения')
        self.assertEqual(result['expectedCount'], 2)

    def test_checked_counter_does_not_become_a_fifth_option(self):
        for text in ('Отмечено 0 из 2.', 'Выбрано: 1 из 2', '2 of 2 selected.', '0 of 2 selected. Submit'):
            frame = self.frame('Какие два поля описывают набор?') + [part(text, .22, .75)]
            result = detect_layout(frame)
            self.assertEqual(result['expectedCount'], 2)
            self.assertEqual(result['texts'][1:], self.values)

    def test_controls_misread_as_letters_need_geometry_and_gutter(self):
        frame = self.frame()
        for i, glyph in ((0, 'LC'), (2, 'L]')):
            frame.append(part(glyph, .205, .39+.09*i, .016, .025))
        layout = detect_layout(frame, aspect=1.6)
        self.assertEqual(layout['selectionMode'], 'multiple')
        self.assertEqual(layout['texts'][1:], self.values)
        for line in frame[-2:]:
            line['w'] = .028  # Ordinary letter-shaped word in the text.
        layout = detect_layout(frame)
        self.assertEqual(layout['selectionMode'], 'single')
        self.assertTrue(layout['texts'][1].startswith('LC'))

    def test_scope_footer_across_neighboring_questions(self):
        frame = self.frame() + [part('Выберите два ответа.', .2, .75),
                                part('Select one.', .2, .95)]
        self.assertEqual(detect_layout(frame)['selectionMode'], 'multiple')


if __name__ == '__main__':
    unittest.main()
