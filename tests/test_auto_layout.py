from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_layout import capture_issue, detect_layout


def part(text,x,y,w=.16,h=.025):
    return dict(text=text,x=x,y=y,w=w,h=h)


QUESTION = 'Какое утверждение неверно?'
VALUES = ['Recall учитывает пропуски', 'Precision учитывает ложные тревоги',
          'Recall всегда равен Precision', 'Precision = TP / (TP + FP)']


class AutoLayoutTests(unittest.TestCase):
    def test_numbered_grid_badges_between_wrapped_lines(self):
        parts = [part('Тема занятия',.15,.09),part('Вопрос 3 из 6',.16,.18),part('10 с',.78,.18,.04),
                 part(QUESTION,.16,.26,.38)]
        for n,value in enumerate(VALUES):
            x = (.16, .52)[n%2]; y = .4+(n//2)*.15
            parts.append(part(str(n+1),x,y+.005,.012,.018))
            if n==1:
                parts.extend([part('Precision учитывает ложные',x+.05,y-.018,.27),
                              part('тревоги',x+.05,y+.018,.09)])
            else:
                parts.append(part(value,x+.05,y,.285))
        parts.append(part('Выберите один вариант.',.15,.8,.28))
        result = detect_layout(parts[::-1],aspect=1.6)
        self.assertEqual(result['texts'],[QUESTION,*VALUES])
        for area in result['regions']:
            self.assertGreater(area['w'],0)
            self.assertGreater(area['h'],0)
            self.assertLessEqual(area['x']+area['w'],1.000001)
            self.assertLessEqual(area['y']+area['h'],1.000001)

    def test_vertical_alphabetic_markers_and_sidebar(self):
        for marks in ['ABCD','АБВГ', '①②③④']:
            parts = [part(QUESTION,.12,.22,.46)]
            for i,value in enumerate(VALUES):
                marker=marks[i]+(')' if marks[0]!='①' else '')
                y=.38+.09*i
                parts.extend([part(marker+' '+value,.13,y,.38),
                              part('Переписка чата',.82,y,.14)])
            self.assertEqual(detect_layout(parts)['texts'],[QUESTION,*VALUES])

    def test_unmarked_numeric_grid_and_list(self):
        for grid in (False,True):
            values=['1 kg','2 days','3.14','4.08']
            parts=[part('Which observation is missing?',.15,.22,.45)]
            for n,value in enumerate(values):
                x=.15+(n%2*.4 if grid else 0)
                y=.35+(n//2*.20 if grid else n*.115)
                parts.append(part(value,x,y,.10))
            self.assertEqual(detect_layout(parts)['texts'][1:],values)

    def test_badge_fused_to_percentage_moves_region_into_answer_column(self):
        # A narrow ordinal can become the leading digit of a Tesseract word.
        def box(text, x, y, width, height):
            return part(text, x/928, y/790, width/928, height/790)
        question = box('What is the conversion rate?', 100, 240, 360, 24)
        options = [box('1', 123, 322, 5, 9), box('2%', 147, 320, 31, 15),
                   box('24%', 123, 372, 55, 15), box('38%', 123, 425, 56, 15),
                   box('4', 122, 480, 7, 9), box('16%', 147, 478, 40, 15)]
        result = detect_layout([question, *options], aspect=928/790)
        self.assertEqual(result['texts'][1:], ['2%', '4%', '8%', '16%'])
        self.assertGreater(result['regions'][2]['x']*928, 130)
        for left in (123, 147):
            options[2] = box('24%', left, 372, 40, 15)
            result = detect_layout([question, *options], aspect=928/790)
            self.assertEqual(result['texts'][2], '24%')

    def test_damaged_progress_counter_cannot_join_wrapped_question(self):
        title = part("Метрики в аналитике", .12, .1, .45)
        counter = part("Вопрос 4 изб", .12, .133, .20)
        intro = part("Магазин посетили 200 человек. Купили 8.", .12, .166, .48)
        prompt = part("Какова конверсия посетителей?", .12, .199, .44)
        options = [part(f"{i}) {answer}", .12, .34 + i * .08, .22)
                   for i, answer in enumerate(("2%", "4%", "8%", "16%"), 1)]
        result = detect_layout([title, counter, intro, prompt, *options])
        self.assertEqual(result['texts'][0], f"{intro['text']} {prompt['text']}")
        self.assertGreater(result['regions'][0]['y'], counter['y'] + .8 * counter['h'])

    def test_supplies_wrapped_question_with_article_and_numbers(self):
        parts=[part('A store had 200 visitors. Eight people bought',.12,.20,.48),
               part('something. What was the conversion rate?',.12,.235,.43)]
        for i,v in enumerate(('1 %','2%','4 %','8%')):
            parts.append(part(str(i+1)+') '+v,.13+.36*(i%2),.4+.2*(i//2),.14))
        self.assertEqual(detect_layout(parts)['texts'][0],
                         'A store had 200 visitors. Eight people bought something. What was the conversion rate?')

    def test_no_guess_for_partial_extra_or_results(self):
        complete=[part(QUESTION,.2,.2,.45)]+[
            part(str(i+1)+') '+v,.2,.35+i*.09,.4) for i,v in enumerate(VALUES)]
        for frame in (complete[:-1],complete+[part('6) Пропущен пятый вариант',.2,.71,.38)],
                      complete+[part('Your score: 3 / 4',.45,.08,.30)],
                      [part('Ожидание участников',.2,.18,.4)]+complete[1:]):
            self.assertIsNone(detect_layout(frame))

    def test_label_order_does_not_depend_on_column_order(self):
        parts=[part(QUESTION,.15,.12,.45)]
        for n,value in enumerate(VALUES):
            parts.append(part(str(n+1)+') '+value,.15+.42*(n//2),.33+.19*(n%2),.37))
        self.assertEqual(detect_layout(parts)['texts'][1:],VALUES)

    def test_rejects_duplicate_options_and_pathological_input(self):
        parts=[part(QUESTION,.2,.2,.45)]+[
            part(str(i+1)+') '+v,.2,.35+i*.09,.4) for i,v in enumerate(('x','x','y','z'))]
        self.assertIsNone(detect_layout(parts))
        self.assertIsNone(detect_layout([part('?',.5,.5)]*3500))


class VariableLayoutTests(unittest.TestCase):
    def frame(self, values, columns=1, marked=True, footer=True, question='Какое поле обозначает количество?'):
        data = [part(question, .09, .16, .56)]
        for i,v in enumerate(values):
            prefix = f'{i+1}) ' if marked else ''
            data.append(part(prefix+v,.09+(i%columns)*(.82/columns),.29+(i//columns)*.072,.69/columns,.018))
        if footer:
            data.append(part('Отправить ответы', .09, data[-1]['y']+.092,.18,.018))
        return data

    def test_two_through_eight_options_in_rows_columns_or_grids(self):
        for n in range(2,9):
            values = [f'Поле {i}' for i in range(1, n+1)]
            for columns in (1,2,3):
                for marked in (False,True):
                    with self.subTest(n=n, columns=columns, marked=marked):
                        result = detect_layout(self.frame(values,columns,marked)[::-1])
                        self.assertIsNotNone(result)
                        self.assertEqual(result['texts'][1:],values)
                        self.assertEqual(len(result['regions']),n+1)

    def test_true_false_statement_without_question_mark(self):
        for values in (['Верно','Неверно'], ['No','Yes']):
            parts = self.frame(values,2,False,False,'Recall учитывает пропущенные положительные случаи.')
            self.assertEqual(detect_layout(parts)['texts'],[parts[0]['text'],*values])

    def test_numeric_options_and_lexical_units(self):
        values = ['1 кг','2 дня','3.14','4.09','5','6/7']
        self.assertEqual(detect_layout(self.frame(values,2,False))['texts'][1:],values)

    def test_explicit_total_incomplete_and_unsupported_counts(self):
        values = ['Имя','Дата','Сумма','Флаг','Число']
        frames = self.frame(values[:4],marked=False,footer=False)
        total = part('Вариантов ответа: 5',.09,.12,.24,.018)
        self.assertIsNone(detect_layout(frames + [total]))
        self.assertIn('прокрути',capture_issue(frames+[total]))
        complete = self.frame(values,marked=False,footer=False)+[total]
        self.assertEqual(detect_layout(complete)['texts'][1:],values)
        unsupported = self.frame([f'Ответ {i}' for i in range(9)])
        self.assertIsNone(detect_layout(unsupported))
        self.assertIsNone(capture_issue([part('Ожидание', .1, .1)]))

    def test_missing_ordinal_interior_and_crop(self):
        parts = self.frame(['Aaa','Bbb','Ccc','Ddd','Eee'],marked=True)
        missing = parts[:3]+parts[4:]
        self.assertIsNone(detect_layout(missing))
        self.assertIsNotNone(capture_issue(missing))
        parts = self.frame(['Aaa','Bbb','Ccc','Ddd'],marked=True,footer=False)
        for span in parts:
            span['y'] += .47
        self.assertIsNone(detect_layout(parts))
        self.assertIsNotNone(capture_issue(parts))

    def test_footer_instruction_shares_a_row_with_submit(self):
        values = ['Amber', 'Black', 'Coral', 'Dark', 'Emerald']
        for n in (2,5):
            frame = self.frame(values[:n], marked=False, footer=False)
            y = frame[-1]['y'] + .095
            frame += [part('Select two answers.', .09, y+.004, .27), part('Submit', .82, y, .11)]
            result = detect_layout(frame)
            self.assertEqual(result['texts'][1:], values[:n])
            self.assertEqual(result['expectedCount'],2)

    def test_missed_or_doubled_gutter_letters(self):
        names=['Amber','Black','Coral','Dark','Emerald','Flint','Golden','Hazel']
        data=self.frame(names, marked=False, footer=True)
        for i, (marker, value) in enumerate(zip(('A.', 'B', 'Cc', None, None, None, 'G', None),names), 1):
            span=data[i]
            span['x'] = .17
            if marker:
                data.append(part(marker,.137,span['y'],.013,.018))
            elif i == 5:
                data.append(part('.',.153,span['y']+.004,.002,.004))
        result=detect_layout(data)
        self.assertEqual(result['texts'][1:],names)

    def test_small_square_badge_corner_stays_out_of_option_text(self):
        values = ['Count', 'Recall', 'Range', 'Median', 'Precision']
        data=self.frame(values,2,False,footer=False)
        for i,span in enumerate(data[1:],1):
            badge_x=span['x']
            span['x'] += .042
            span['w'] -= .042
            data.append(part(str(i),badge_x,span['y'],.012,.014))
            if i in (3,5):
                data.append(part('|',badge_x+.015,span['y']+.018,.002,.003))
        self.assertEqual(detect_layout(data)['texts'][1:],values)
